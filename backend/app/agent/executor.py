from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pydantic import ValidationError

from app.agent.state import AgentState, ChunkDict
from app.agent.stream_context import emit_event
from app.schemas.tools.company_comparator import CompanyComparatorInput
from app.schemas.tools.document_finder import DocumentFinderInput
from app.schemas.tools.document_retrieval import DocumentRetrievalInput
from app.schemas.tools.financial_data import FinancialDataInput
from app.schemas.tools.portfolio_analysis import PortfolioAnalysisInput
from app.schemas.tools.web_search import WebSearchInput
from app.tools import TOOL_REGISTRY, ToolError

_MAX_RETRIEVAL_QUERY_LEN = 500

_TOOL_INPUT_MODELS = {
    "company_comparator": CompanyComparatorInput,
    "document_finder": DocumentFinderInput,
    "document_retrieval": DocumentRetrievalInput,
    "financial_data": FinancialDataInput,
    "portfolio_analysis": PortfolioAnalysisInput,
    "web_search": WebSearchInput,
}


async def _run_step(i: int, step: dict, state: AgentState) -> tuple[str, dict, Any]:
    """
    Execute a single plan step. Returns (step_key, envelope, live_result).
    live_result is the raw tool output before serialization; None on failure.
    Never raises — failures return a failure envelope so siblings still run.
    """
    logger = structlog.get_logger(__name__)
    tool_name = step["tool_name"]
    step_key = f"step_{i}"
    raw_input: dict = dict(step.get("input") or {})

    try:
        if tool_name in ("document_retrieval", "document_finder"):
            raw_input["user_id"] = state["user_id"]
            raw_input["conversation_id"] = state["conversation_id"]
        if tool_name == "portfolio_analysis":
            raw_input["user_id"] = state["user_id"]

        if tool_name == "document_retrieval":
            if isinstance(raw_input.get("query"), str):
                raw_input["query"] = raw_input["query"][:_MAX_RETRIEVAL_QUERY_LEN]

        tool_input = _TOOL_INPUT_MODELS[tool_name].model_validate(raw_input)

        emit_event({"type": "tool_call", "tool_name": tool_name, "step_id": step_key, "status": "running"})
        result = await TOOL_REGISTRY[tool_name](tool_input)
        emit_event({"type": "tool_call", "tool_name": tool_name, "step_id": step_key, "status": "complete"})

        envelope: dict = {"tool_name": tool_name, "status": "ok", "data": result.model_dump(mode="json")}
        return step_key, envelope, result

    except (ToolError, ValidationError) as e:
        logger.warning(
            "executor_tool_error",
            tool_name=tool_name,
            step_id=step_key,
            error_type=type(e).__name__,
            error=str(e),
        )
        emit_event({"type": "tool_call", "tool_name": tool_name, "step_id": step_key, "status": "error"})
        return step_key, {"tool_name": tool_name, "status": "error", "error": str(e)}, None

    except Exception as e:
        logger.error(
            "executor_tool_error",
            tool_name=tool_name,
            step_id=step_key,
            error_type=type(e).__name__,
            error=str(e),
        )
        emit_event({"type": "tool_call", "tool_name": tool_name, "step_id": step_key, "status": "error"})
        return step_key, {"tool_name": tool_name, "status": "error", "error": str(e)}, None


async def executor_node(state: AgentState) -> dict:
    logger = structlog.get_logger(__name__)
    emit_event({"type": "node_update", "node": "executor_node", "status": "running"})
    try:
        plan: list[dict] = list(state.get("plan") or [])

        if not plan:
            return {
                "tool_results": {},
                "retrieved_chunks": [],
                "reranked_chunks": [],
                "rag_used": False,
            }

        tool_results: dict[str, Any] = {}
        all_chunks: list[ChunkDict] = []
        error_count = 0

        # Separate document_retrieval steps (run first, sequentially) from others
        retrieval_steps = [(i, step) for i, step in enumerate(plan) if step["tool_name"] == "document_retrieval"]
        other_steps = [(i, step) for i, step in enumerate(plan) if step["tool_name"] != "document_retrieval"]

        # Run document_retrieval steps sequentially first so RAG context
        # is available before the synthesizer and chunk count is authoritative
        for i, step in retrieval_steps:
            step_key, envelope, live_result = await _run_step(i, step, state)
            tool_results[step_key] = envelope
            if envelope["status"] == "error":
                error_count += 1
            elif live_result is not None:
                all_chunks.extend(_normalize_chunks(live_result))

        # Run remaining steps concurrently with bounded concurrency ≤4
        if other_steps:
            semaphore = asyncio.Semaphore(4)

            async def _run_with_semaphore(idx: int, s: dict) -> tuple[str, dict, Any]:
                async with semaphore:
                    return await _run_step(idx, s, state)

            gathered = await asyncio.gather(*[_run_with_semaphore(i, step) for i, step in other_steps])

            for step_key, envelope, live_result in gathered:
                tool_results[step_key] = envelope
                if envelope["status"] == "error":
                    error_count += 1
                    continue

                # Document finder follow-up: when a filing was successfully fetched,
                # immediately run a retrieval so the new doc is queryable this turn.
                if envelope["tool_name"] == "document_finder" and live_result is not None:
                    if hasattr(live_result, "status") and live_result.status in ("ready", "duplicate"):
                        retrieval_key = f"{step_key}_retrieval"
                        try:
                            retrieval_input = DocumentRetrievalInput(
                                query=state["query"][:_MAX_RETRIEVAL_QUERY_LEN],
                                user_id=state["user_id"],
                                conversation_id=state["conversation_id"],
                            )
                            emit_event({"type": "tool_call", "tool_name": "document_retrieval", "step_id": retrieval_key, "status": "running"})
                            retrieval_result = await TOOL_REGISTRY["document_retrieval"](retrieval_input)
                            emit_event({"type": "tool_call", "tool_name": "document_retrieval", "step_id": retrieval_key, "status": "complete"})
                            tool_results[retrieval_key] = {
                                "tool_name": "document_retrieval",
                                "status": "ok",
                                "data": retrieval_result.model_dump(mode="json"),
                            }
                            all_chunks.extend(_normalize_chunks(retrieval_result))
                        except Exception as e:
                            logger.warning(
                                "executor_finder_retrieval_failed",
                                step_key=step_key,
                                error=str(e),
                            )

        all_chunks = _dedup_chunks(all_chunks)
        reranked_chunks = all_chunks  # TODO: replace with real reranker
        rag_used = len(all_chunks) > 0

        if rag_used:
            emit_event({"type": "sources", "chunks": reranked_chunks})

        logger.debug(
            "executor_completed",
            step_count=len(plan),
            chunk_count=len(all_chunks),
            error_count=error_count,
        )
        return {
            "tool_results": tool_results,
            "retrieved_chunks": all_chunks,
            "reranked_chunks": reranked_chunks,
            "rag_used": rag_used,
        }

    except Exception as e:
        logger.error("executor_error", error=str(e))
        return {"error": str(e)}


def _normalize_chunks(raw: Any) -> list[ChunkDict]:
    # DocumentRetrievalOutput is a Pydantic model with a .chunks list of ChunkResult objects.
    # The tool always returns this type — unwrap it before any other checks.
    if hasattr(raw, "chunks"):
        return [
            ChunkDict(
                content=chunk.content,
                metadata={**(chunk.metadata or {}), "chunk_id": str(chunk.chunk_id), "document_id": str(chunk.document_id)},
                score=chunk.similarity_score,
            )
            for chunk in raw.chunks
            if chunk.content
        ]
    if not isinstance(raw, list):
        return []
    out: list[ChunkDict] = []
    for item in raw:
        if isinstance(item, dict) and "content" in item:
            base_meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if "chunk_id" in item:
                base_meta["chunk_id"] = str(item["chunk_id"])
            if "document_id" in item:
                base_meta["document_id"] = str(item["document_id"])
            out.append(
                ChunkDict(
                    content=str(item["content"]),
                    metadata=base_meta,
                    score=float(item.get("score", 0.0)),
                )
            )
    return out


def _dedup_chunks(chunks: list[ChunkDict]) -> list[ChunkDict]:
    seen: set[str] = set()
    out: list[ChunkDict] = []
    for chunk in chunks:
        key = chunk["content"]
        if key not in seen:
            seen.add(key)
            out.append(chunk)
    return out
