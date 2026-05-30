from __future__ import annotations

from unittest.mock import AsyncMock

from langgraph.graph import END, StateGraph

from app.agent.graph import (
    _build_graph,
    route_after_executor,
    route_after_planner,
    route_after_router,
    route_after_tool_selector,
)
from app.agent.state import AgentState


# ---------------------------------------------------------------------------
# route_after_router
# ---------------------------------------------------------------------------

def test_router_routes_simple():
    assert route_after_router({"classification": "simple"}) == "tool_selector_node"


def test_router_routes_complex():
    assert route_after_router({"classification": "complex"}) == "planner_node"


def test_router_routes_ingest():
    assert route_after_router({"classification": "ingest"}) == END


def test_router_routes_unknown_falls_back_to_tool_selector():
    assert route_after_router({"classification": "BOGUS"}) == "tool_selector_node"


def test_router_missing_key_falls_back_to_planner():
    # route_after_router defaults classification to "complex" when key is absent
    assert route_after_router({}) == "planner_node"


def test_router_error_short_circuits():
    assert route_after_router({"error": "router crashed"}) == END


# ---------------------------------------------------------------------------
# route_after_tool_selector
# ---------------------------------------------------------------------------

def test_tool_selector_routes_to_executor():
    assert route_after_tool_selector({"error": None}) == "executor_node"


def test_tool_selector_routes_to_executor_no_error_key():
    assert route_after_tool_selector({}) == "executor_node"


def test_tool_selector_error_short_circuits():
    assert route_after_tool_selector({"error": "boom"}) == END


# ---------------------------------------------------------------------------
# route_after_planner
# ---------------------------------------------------------------------------

def test_planner_routes_to_executor():
    assert route_after_planner({"error": None}) == "executor_node"


def test_planner_routes_to_executor_no_error_key():
    assert route_after_planner({}) == "executor_node"


def test_planner_error_short_circuits():
    assert route_after_planner({"error": "boom"}) == END


# ---------------------------------------------------------------------------
# route_after_executor
# ---------------------------------------------------------------------------

def test_executor_routes_to_synthesizer():
    assert route_after_executor({"error": None}) == "synthesizer_node"


def test_executor_routes_to_synthesizer_no_error_key():
    assert route_after_executor({}) == "synthesizer_node"


def test_executor_error_short_circuits():
    assert route_after_executor({"error": "boom"}) == END


# ---------------------------------------------------------------------------
# Integration test helpers
# ---------------------------------------------------------------------------

_BASE_STATE: dict = {
    "user_id": "test-user",
    "conversation_id": "test-conv",
    "analyst_profile": {},
    "query": "test query",
    "classification": "simple",
    "plan": [],
    "tool_results": {},
    "retrieved_chunks": [],
    "reranked_chunks": [],
    "retrieval_quality_score": 1.0,
    "rag_used": False,
    "relevance_score": None,
    "retry_count": 0,
    "conversation_summary": "",
    "recent_messages": [],
    "final_output": "",
    "error": None,
    "model": "gpt-4o",
    "has_uploaded_documents": False,
}


def _build_test_graph(
    mock_router,
    mock_tool_selector,
    mock_planner,
    mock_executor,
    mock_synthesizer,
):
    builder = StateGraph(AgentState)
    builder.add_node("router_node", mock_router)
    builder.add_node("tool_selector_node", mock_tool_selector)
    builder.add_node("planner_node", mock_planner)
    builder.add_node("executor_node", mock_executor)
    builder.add_node("synthesizer_node", mock_synthesizer)
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
        {"executor_node": "executor_node", END: END},
    )
    builder.add_conditional_edges(
        "planner_node",
        route_after_planner,
        {"executor_node": "executor_node", END: END},
    )
    builder.add_conditional_edges(
        "executor_node",
        route_after_executor,
        {"synthesizer_node": "synthesizer_node", END: END},
    )
    builder.add_edge("synthesizer_node", END)
    return builder.compile()


# ---------------------------------------------------------------------------
# Integration: simple path topology
# ---------------------------------------------------------------------------

async def test_simple_path_topology():
    call_order: list[str] = []

    async def mock_router(s):
        call_order.append("router")
        return {"classification": "simple"}

    async def mock_tool_selector(s):
        call_order.append("tool_selector")
        return {"plan": []}

    async def mock_planner(s):
        call_order.append("planner")
        return {"plan": []}

    async def mock_executor(s):
        call_order.append("executor")
        return {"tool_results": {}, "retrieved_chunks": [], "reranked_chunks": [], "rag_used": False}

    async def mock_synthesizer(s):
        call_order.append("synthesizer")
        return {"final_output": "answer"}

    graph = _build_test_graph(
        mock_router, mock_tool_selector, mock_planner, mock_executor, mock_synthesizer
    )
    await graph.ainvoke(dict(_BASE_STATE))

    assert call_order == ["router", "tool_selector", "executor", "synthesizer"]


# ---------------------------------------------------------------------------
# Integration: complex path topology
# ---------------------------------------------------------------------------

async def test_complex_path_topology():
    call_order: list[str] = []

    async def mock_router(s):
        call_order.append("router")
        return {"classification": "complex"}

    async def mock_tool_selector(s):
        call_order.append("tool_selector")
        return {"plan": []}

    async def mock_planner(s):
        call_order.append("planner")
        return {"plan": []}

    async def mock_executor(s):
        call_order.append("executor")
        return {"tool_results": {}, "retrieved_chunks": [], "reranked_chunks": [], "rag_used": False}

    async def mock_synthesizer(s):
        call_order.append("synthesizer")
        return {"final_output": "answer"}

    graph = _build_test_graph(
        mock_router, mock_tool_selector, mock_planner, mock_executor, mock_synthesizer
    )
    await graph.ainvoke(dict(_BASE_STATE))

    assert call_order == ["router", "planner", "executor", "synthesizer"]


# ---------------------------------------------------------------------------
# Integration: ingest exits at router
# ---------------------------------------------------------------------------

async def test_ingest_exits_at_router():
    call_order: list[str] = []

    async def mock_router(s):
        call_order.append("router")
        return {"classification": "ingest"}

    async def mock_tool_selector(s):
        call_order.append("tool_selector")
        return {"plan": []}

    async def mock_planner(s):
        call_order.append("planner")
        return {"plan": []}

    async def mock_executor(s):
        call_order.append("executor")
        return {"tool_results": {}, "retrieved_chunks": [], "reranked_chunks": [], "rag_used": False}

    async def mock_synthesizer(s):
        call_order.append("synthesizer")
        return {"final_output": "answer"}

    graph = _build_test_graph(
        mock_router, mock_tool_selector, mock_planner, mock_executor, mock_synthesizer
    )
    result = await graph.ainvoke(dict(_BASE_STATE))

    assert call_order == ["router"]
    assert result.get("final_output", "") == ""


# ---------------------------------------------------------------------------
# Integration: error short-circuits immediately
# ---------------------------------------------------------------------------

async def test_error_short_circuits():
    call_order: list[str] = []

    async def mock_router(s):
        call_order.append("router")
        return {"error": "router crashed"}

    async def mock_tool_selector(s):
        call_order.append("tool_selector")
        return {"plan": []}

    async def mock_planner(s):
        call_order.append("planner")
        return {"plan": []}

    async def mock_executor(s):
        call_order.append("executor")
        return {"tool_results": {}, "retrieved_chunks": [], "reranked_chunks": [], "rag_used": False}

    async def mock_synthesizer(s):
        call_order.append("synthesizer")
        return {"final_output": "answer"}

    graph = _build_test_graph(
        mock_router, mock_tool_selector, mock_planner, mock_executor, mock_synthesizer
    )
    result = await graph.ainvoke(dict(_BASE_STATE))

    assert call_order == ["router"]
    assert result.get("error") == "router crashed"
