from __future__ import annotations

import structlog
from langgraph.graph import END, StateGraph

from app.agent.evaluator import evaluator_node
from app.agent.executor import executor_node
from app.agent.planner import planner_node
from app.agent.router import router_node
from app.agent.state import AgentState
from app.agent.synthesizer import synthesizer_node

log = structlog.get_logger(__name__)

__all__ = ["route_after_router", "route_after_evaluator", "compiled_graph", "_build_graph"]


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


def _build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("router_node", router_node)
    builder.add_node("planner_node", planner_node)
    builder.add_node("executor_node", executor_node)
    builder.add_node("evaluator_node", evaluator_node)
    builder.add_node("synthesizer_node", synthesizer_node)

    builder.set_entry_point("router_node")

    builder.add_conditional_edges(
        "router_node",
        route_after_router,
        {
            "planner_node": "planner_node",
            "executor_node": "executor_node",
            END: END,
        },
    )

    builder.add_edge("planner_node", "executor_node")
    builder.add_edge("executor_node", "evaluator_node")

    builder.add_conditional_edges(
        "evaluator_node",
        route_after_evaluator,
        {
            "executor_node": "executor_node",
            "synthesizer_node": "synthesizer_node",
        },
    )

    builder.add_edge("synthesizer_node", END)

    return builder


compiled_graph = _build_graph().compile()
log.debug("agent_graph_compiled")
