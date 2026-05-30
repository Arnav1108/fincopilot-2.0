from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from app.agent.state import AgentState, ChunkDict
from app.agent.stream_context import emit_event
from app.schemas.tools.company_comparator import CompanyComparatorInput
from app.schemas.tools.document_finder import DocumentFinderInput
from app.schemas.tools.document_retrieval import DocumentRetrievalInput
from app.schemas.tools.financial_calculator import FinancialCalculatorInput
from app.schemas.tools.financial_data import FinancialDataInput
from app.schemas.tools.web_search import WebSearchInput
from app.tools import TOOL_REGISTRY, ToolError

_MAX_RETRIEVAL_QUERY_LEN = 500  # embedding search quality doesn't improve beyond this

_TOOL_INPUT_MODELS = {
    "company_comparator": CompanyComparatorInput,
    "document_finder": DocumentFinderInput,
    "document_retrieval": DocumentRetrievalInput,
    "financial_calculator": FinancialCalculatorInput,
    "financial_data": FinancialDataInput,
    "web_search": WebSearchInput,
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
                input_template = step["input_template"]
                if isinstance(input_template, dict):
                    # Directly substitute known placeholders in dict values
                    rendered_dict = {}
                    for k, v in input_template.items():
                        if isinstance(v, str):
                            rendered_dict[k] = v.replace("{query}", state["query"]).replace("{user_id}", state["user_id"]).replace("{conversation_id}", state["conversation_id"])
                        else:
                            rendered_dict[k] = v
                    rendered = json.dumps(rendered_dict)
                else:
                    rendered = str(input_template).replace("{query}", state["query"]).replace("{user_id}", state["user_id"]).replace("{conversation_id}", state["conversation_id"])
                logger.warning("executor_rendered_input", step_id=step_id, tool_name=step["tool_name"], rendered=rendered)
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
                        elif step["tool_name"] == "document_finder":
                            if set(tool_input.keys()) == {"input"}:
                                tool_input = {"query": tool_input["input"]}
                            tool_input["user_id"] = state["user_id"]
                            tool_input["conversation_id"] = state["conversation_id"]
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

        # If document_finder returned a ready/duplicate result, run an immediate retrieval
        for sid in resolved:
            step = step_map.get(sid)
            if step and step["tool_name"] == "document_finder":
                result = tool_results.get(sid)
                if hasattr(result, "status") and result.status in ("ready", "duplicate"):
                    try:
                        retrieval_result = await TOOL_REGISTRY["document_retrieval"](
                            DocumentRetrievalInput(
                                query=state["query"][:_MAX_RETRIEVAL_QUERY_LEN],
                                user_id=state["user_id"],
                                conversation_id=state["conversation_id"],
                            )
                        )
                        tool_results[f"{sid}_retrieval"] = retrieval_result
                        emit_event({"type": "tool_call", "tool_name": "document_retrieval", "step_id": f"{sid}_retrieval", "status": "complete"})
                    except Exception as e:
                        logger.warning("document_finder_retrieval_failed", error=str(e))

        # Collect document_retrieval results into flat chunk list
        all_chunks: list[ChunkDict] = []
        for sid, result in tool_results.items():
            step = step_map.get(sid)
            tool_name = step["tool_name"] if step else None
            if tool_name == "document_retrieval" or sid.endswith("_retrieval"):
                all_chunks.extend(_normalize_chunks(result))

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
