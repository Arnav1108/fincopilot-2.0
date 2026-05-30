from __future__ import annotations

import structlog
from langgraph.graph import END, StateGraph

from app.agent.executor import executor_node
from app.agent.planner import planner_node
from app.agent.router import router_node
from app.agent.state import AgentState
from app.agent.synthesizer import synthesizer_node
from app.agent.tool_selector import tool_selector_node

log = structlog.get_logger(__name__)

__all__ = [
    "route_after_router",
    "route_after_tool_selector",
    "route_after_planner",
    "route_after_executor",
    "compiled_graph",
    "_build_graph",
]


def route_after_router(state: AgentState) -> str:
    if state.get("error"):
        return END
    classification = state.get("classification", "complex")
    if classification == "ingest":
        return END
    elif classification == "complex":
        return "planner_node"
    else:
        return "tool_selector_node"


def route_after_tool_selector(state: AgentState) -> str:
    if state.get("error"):
        return END
    return "executor_node"


def route_after_planner(state: AgentState) -> str:
    if state.get("error"):
        return END
    return "executor_node"


def route_after_executor(state: AgentState) -> str:
    if state.get("error"):
        return END
    return "synthesizer_node"


def _build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("router_node", router_node)
    builder.add_node("tool_selector_node", tool_selector_node)
    builder.add_node("planner_node", planner_node)
    builder.add_node("executor_node", executor_node)
    builder.add_node("synthesizer_node", synthesizer_node)

    builder.set_entry_point("router_node")

    builder.add_conditional_edges(
        "router_node",
        route_after_router,
        {
            "tool_selector_node": "tool_selector_node",
            "planner_node": "planner_node",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "tool_selector_node",
        route_after_tool_selector,
        {
            "executor_node": "executor_node",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "planner_node",
        route_after_planner,
        {
            "executor_node": "executor_node",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "executor_node",
        route_after_executor,
        {
            "synthesizer_node": "synthesizer_node",
            END: END,
        },
    )

    builder.add_edge("synthesizer_node", END)

    return builder


compiled_graph = _build_graph().compile()
log.debug("agent_graph_compiled")
