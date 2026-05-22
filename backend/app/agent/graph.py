from __future__ import annotations

import structlog
from langgraph.graph import END

from app.agent.state import AgentState

log = structlog.get_logger(__name__)

__all__ = ["route_after_router", "route_after_evaluator"]


def route_after_router(state: AgentState) -> str:
    classification = state.get("classification", "simple")
    if classification == "ingest":
        next_node = END
    elif classification == "complex":
        next_node = "planner_node"
    else:
        next_node = "executor_node"
    log.debug("route_after_router", classification=classification, next=next_node)
    return next_node


def route_after_evaluator(state: AgentState) -> str:
    score = state.get("retrieval_quality_score", 1.0)
    retry_count = state.get("retry_count", 0)
    if score < 0.6 and retry_count <= 2:
        next_node = "executor_node"
    else:
        next_node = "synthesizer_node"
    log.debug("route_after_evaluator", score=score, retry_count=retry_count, next=next_node)
    return next_node
