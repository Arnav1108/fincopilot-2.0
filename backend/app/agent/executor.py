from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from app.agent.state import AgentState, ChunkDict
from app.agent.stream_context import emit_event
from app.schemas.tools.document_retrieval import DocumentRetrievalInput
from app.schemas.tools.financial_calculator import FinancialCalculatorInput
from app.schemas.tools.financial_data import FinancialDataInput
from app.schemas.tools.news_fetch import NewsFetchInput
from app.schemas.tools.sec_filing import SECFilingInput
from app.tools import TOOL_REGISTRY, ToolError

_MAX_RETRIEVAL_QUERY_LEN = 500  # embedding search quality doesn't improve beyond this

_TOOL_INPUT_MODELS = {
    "document_retrieval": DocumentRetrievalInput,
    "financial_calculator": FinancialCalculatorInput,
    "financial_data": FinancialDataInput,
    "news_fetch": NewsFetchInput,
    "sec_filing": SECFilingInput,
}


async def executor_node(state: AgentState) -> dict:
    logger = structlog.get_logger(__name__)
    emit_event({"type": "node_update", "node": "executor_node", "status": "running"})
    try:
        classification = state["classification"]
        existing_tool_results: dict[str, Any] = dict(state.get("tool_results") or {})

        if classification in ("simple", "ingest"):
            emit_event({"type": "tool_call", "tool_name": "document_retrieval", "step_id": "retrieval", "status": "running"})
            try:
                raw = await TOOL_REGISTRY["document_retrieval"](
                    DocumentRetrievalInput(
                        query=state["query"][:_MAX_RETRIEVAL_QUERY_LEN],
                        user_id=state["user_id"],
                        conversation_id=state["conversation_id"],
                    )
                )
                emit_event({"type": "tool_call", "tool_name": "document_retrieval", "step_id": "retrieval", "status": "complete"})
            except Exception:
                emit_event({"type": "tool_call", "tool_name": "document_retrieval", "step_id": "retrieval", "status": "error"})
                raise
            chunks = _normalize_chunks(raw)
            chunks = _dedup_chunks(chunks)
            reranked_chunks = chunks  # TODO: replace with real reranker in feature/agent-stream
            emit_event({"type": "sources", "chunks": reranked_chunks})
            logger.debug(
                "executor_completed",
                step_count=1,
                chunk_count=len(chunks),
                error_count=0,
            )
            return {
                "tool_results": existing_tool_results,
                "retrieved_chunks": chunks,
                "reranked_chunks": reranked_chunks,
                "rag_used": len(chunks) > 0,
            }

        # --- complex: execute plan in dependency order ---
        plan = state.get("plan") or []
        tool_results: dict[str, Any] = {}
        resolved: set[str] = set()
        error_count = 0

        # Build adjacency: step_id -> PlanStep
        step_map = {step["id"]: step for step in plan}
        remaining = list(step_map.keys())

        while remaining:
            # Collect steps whose dependencies are all resolved
            ready = [sid for sid in remaining if all(d in resolved for d in step_map[sid]["dependencies"])]
            if not ready:
                # Circular or unresolvable — bail out
                logger.warning("executor_unresolvable_dependencies", remaining=remaining)
                break

            async def _run_step(step_id: str) -> tuple[str, Any]:
                step = step_map[step_id]
                emit_event({"type": "tool_call", "tool_name": step["tool_name"], "step_id": step_id, "status": "running"})
                rendered = step["input_template"].format_map(
                    {
                        "query": state["query"],
                        "user_id": state["user_id"],
                        "conversation_id": state["conversation_id"],
                        **{sid: str(tool_results.get(sid, "")) for sid in resolved},
                    }
                )
                try:
                    tool_input: Any
                    try:
                        tool_input = json.loads(rendered)
                        if not isinstance(tool_input, dict):
                            tool_input = {"input": rendered}
                    except (json.JSONDecodeError, ValueError):
                        tool_input = {"input": rendered}

                    input_cls = _TOOL_INPUT_MODELS.get(step["tool_name"])
                    if input_cls is not None and isinstance(tool_input, dict):
                        if step["tool_name"] == "document_retrieval":
                            # Planner templates are often plain strings, not full JSON objects.
                            # When the template wasn't JSON, tool_input is {"input": rendered}.
                            # Reshape it to DocumentRetrievalInput's expected keys.
                            if set(tool_input.keys()) == {"input"}:
                                tool_input = {"query": tool_input["input"]}
                            tool_input.setdefault("user_id", state["user_id"])
                            tool_input.setdefault("conversation_id", state["conversation_id"])
                            # Planner templates can expand into very long strings that hurt
                            # embedding quality and fail schema validation. Truncate hard.
                            if isinstance(tool_input.get("query"), str):
                                tool_input["query"] = tool_input["query"][:_MAX_RETRIEVAL_QUERY_LEN]
                        tool_input = input_cls.model_validate(tool_input)
                    result = await TOOL_REGISTRY[step["tool_name"]](tool_input)
                    emit_event({"type": "tool_call", "tool_name": step["tool_name"], "step_id": step_id, "status": "complete"})
                    return step_id, result
                except ToolError as e:
                    logger.warning(
                        "executor_tool_error",
                        tool_name=step["tool_name"],
                        step_id=step_id,
                        error=str(e),
                    )
                    emit_event({"type": "tool_call", "tool_name": step["tool_name"], "step_id": step_id, "status": "error"})
                    return step_id, {"error": str(e)}

            results = await asyncio.gather(*[_run_step(sid) for sid in ready])

            for sid, result in results:
                tool_results[sid] = result
                resolved.add(sid)
                if isinstance(result, dict) and "error" in result:
                    error_count += 1

            remaining = [sid for sid in remaining if sid not in resolved]

        # Collect document_retrieval results into flat chunk list
        all_chunks: list[ChunkDict] = []
        for sid in resolved:
            step = step_map.get(sid)
            if step and step["tool_name"] == "document_retrieval":
                raw = tool_results.get(sid)
                all_chunks.extend(_normalize_chunks(raw))

        chunks = _dedup_chunks(all_chunks)
        reranked_chunks = chunks  # TODO: replace with real reranker in feature/agent-stream
        emit_event({"type": "sources", "chunks": reranked_chunks})

        merged = {**tool_results, **existing_tool_results}

        logger.debug(
            "executor_completed",
            step_count=len(plan),
            chunk_count=len(chunks),
            error_count=error_count,
        )
        return {
            "tool_results": merged,
            "retrieved_chunks": chunks,
            "reranked_chunks": reranked_chunks,
            "rag_used": len(chunks) > 0,
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
            out.append(
                ChunkDict(
                    content=str(item["content"]),
                    metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else None,
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
