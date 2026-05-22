from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from app.agent.state import AgentState, ChunkDict
from app.tools import TOOL_REGISTRY, ToolError


async def executor_node(state: AgentState) -> dict:
    logger = structlog.get_logger(__name__)
    try:
        classification = state["classification"]
        existing_tool_results: dict[str, Any] = dict(state.get("tool_results") or {})

        if classification in ("simple", "ingest"):
            raw = await TOOL_REGISTRY["document_retrieval"](
                {"query": state["query"], "user_id": state["user_id"]}
            )
            chunks = _normalize_chunks(raw)
            chunks = _dedup_chunks(chunks)
            reranked_chunks = chunks  # TODO: replace with real reranker in feature/agent-stream
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
                rendered = step["input_template"].format_map(
                    {
                        "query": state["query"],
                        "user_id": state["user_id"],
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

                    result = await TOOL_REGISTRY[step["tool_name"]](tool_input)
                    return step_id, result
                except ToolError as e:
                    logger.warning(
                        "executor_tool_error",
                        tool_name=step["tool_name"],
                        step_id=step_id,
                        error=str(e),
                    )
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
        }

    except Exception as e:
        logger.error("executor_error", error=str(e))
        return {"error": str(e)}


def _normalize_chunks(raw: Any) -> list[ChunkDict]:
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
