from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from langgraph.graph import END, StateGraph

from app.agent.graph import (
    _build_graph,
    route_after_evaluator,
    route_after_router,
)
from app.agent.state import AgentState


# ---------------------------------------------------------------------------
# route_after_router (5 tests)
# ---------------------------------------------------------------------------

def test_router_routes_simple():
    assert route_after_router({"classification": "simple"}) == "executor_node"


def test_router_routes_complex():
    assert route_after_router({"classification": "complex"}) == "planner_node"


def test_router_routes_ingest():
    assert route_after_router({"classification": "ingest"}) == END


def test_router_routes_unknown_falls_back():
    assert route_after_router({"classification": "BOGUS"}) == "executor_node"


def test_router_missing_key_falls_back():
    assert route_after_router({}) == "executor_node"


# ---------------------------------------------------------------------------
# route_after_evaluator (7 tests)
# ---------------------------------------------------------------------------

def test_evaluator_high_score_routes_to_synthesizer():
    assert route_after_evaluator({"retrieval_quality_score": 0.9, "retry_count": 0}) == "synthesizer_node"


def test_evaluator_low_score_retry_0_routes_to_executor():
    assert route_after_evaluator({"retrieval_quality_score": 0.5, "retry_count": 0}) == "executor_node"


def test_evaluator_low_score_retry_1_routes_to_executor():
    assert route_after_evaluator({"retrieval_quality_score": 0.5, "retry_count": 1}) == "executor_node"


def test_evaluator_low_score_retry_2_routes_to_executor():
    assert route_after_evaluator({"retrieval_quality_score": 0.5, "retry_count": 2}) == "executor_node"


def test_evaluator_low_score_retry_3_routes_to_synthesizer():
    assert route_after_evaluator({"retrieval_quality_score": 0.5, "retry_count": 3}) == "synthesizer_node"


def test_evaluator_boundary_score_routes_to_synthesizer():
    assert route_after_evaluator({"retrieval_quality_score": 0.6, "retry_count": 0}) == "synthesizer_node"


def test_evaluator_missing_keys_routes_to_synthesizer():
    assert route_after_evaluator({}) == "synthesizer_node"


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
    "retrieval_quality_score": 0.0,
    "retry_count": 0,
    "conversation_summary": "",
    "recent_messages": [],
    "final_output": "",
    "error": None,
    "model": "gpt-4o",
}


def _build_test_graph(
    mock_router, mock_planner, mock_executor, mock_evaluator, mock_synthesizer
):
    builder = StateGraph(AgentState)
    builder.add_node("router_node", mock_router)
    builder.add_node("planner_node", mock_planner)
    builder.add_node("executor_node", mock_executor)
    builder.add_node("evaluator_node", mock_evaluator)
    builder.add_node("synthesizer_node", mock_synthesizer)
    builder.set_entry_point("router_node")
    builder.add_conditional_edges(
        "router_node",
        route_after_router,
        {"planner_node": "planner_node", "executor_node": "executor_node", END: END},
    )
    builder.add_edge("planner_node", "executor_node")
    builder.add_edge("executor_node", "evaluator_node")
    builder.add_conditional_edges(
        "evaluator_node",
        route_after_evaluator,
        {"executor_node": "executor_node", "synthesizer_node": "synthesizer_node"},
    )
    builder.add_edge("synthesizer_node", END)
    return builder.compile()


# ---------------------------------------------------------------------------
# Integration tests (I1–I5)
# ---------------------------------------------------------------------------

async def test_graph_simple_path():
    call_order: list[str] = []
    state = dict(_BASE_STATE)

    async def mock_router(s):
        call_order.append("router")
        return {"classification": "simple"}

    async def mock_planner(s):
        call_order.append("planner")
        return {"plan": []}

    async def mock_executor(s):
        call_order.append("executor")
        return {"tool_results": {}, "retrieved_chunks": [], "reranked_chunks": []}

    async def mock_evaluator(s):
        call_order.append("evaluator")
        return {"retrieval_quality_score": 0.9, "retry_count": 0, "query": "test query"}

    async def mock_synthesizer(s):
        call_order.append("synthesizer")
        return {"final_output": "answer"}

    graph = _build_test_graph(mock_router, mock_planner, mock_executor, mock_evaluator, mock_synthesizer)
    await graph.ainvoke(state)

    assert call_order == ["router", "executor", "evaluator", "synthesizer"]


async def test_graph_complex_path():
    call_order: list[str] = []
    state = dict(_BASE_STATE)

    async def mock_router(s):
        call_order.append("router")
        return {"classification": "complex"}

    async def mock_planner(s):
        call_order.append("planner")
        return {"plan": []}

    async def mock_executor(s):
        call_order.append("executor")
        return {"tool_results": {}, "retrieved_chunks": [], "reranked_chunks": []}

    async def mock_evaluator(s):
        call_order.append("evaluator")
        return {"retrieval_quality_score": 0.9, "retry_count": 0, "query": "test query"}

    async def mock_synthesizer(s):
        call_order.append("synthesizer")
        return {"final_output": "answer"}

    graph = _build_test_graph(mock_router, mock_planner, mock_executor, mock_evaluator, mock_synthesizer)
    await graph.ainvoke(state)

    assert call_order == ["router", "planner", "executor", "evaluator", "synthesizer"]


async def test_graph_ingest_path():
    call_order: list[str] = []
    state = dict(_BASE_STATE)

    async def mock_router(s):
        call_order.append("router")
        return {"classification": "ingest"}

    async def mock_planner(s):
        call_order.append("planner")
        return {"plan": []}

    async def mock_executor(s):
        call_order.append("executor")
        return {"tool_results": {}, "retrieved_chunks": [], "reranked_chunks": []}

    async def mock_evaluator(s):
        call_order.append("evaluator")
        return {"retrieval_quality_score": 0.9, "retry_count": 0, "query": "test query"}

    async def mock_synthesizer(s):
        call_order.append("synthesizer")
        return {"final_output": "answer"}

    graph = _build_test_graph(mock_router, mock_planner, mock_executor, mock_evaluator, mock_synthesizer)
    result = await graph.ainvoke(state)

    assert call_order == ["router"]
    assert result.get("final_output", "") == ""


async def test_graph_retry_path_two_retries():
    call_order: list[str] = []
    state = dict(_BASE_STATE)

    evaluator_returns = iter([
        {"retrieval_quality_score": 0.4, "retry_count": 1, "query": "reformulated-1"},
        {"retrieval_quality_score": 0.4, "retry_count": 2, "query": "reformulated-2"},
        {"retrieval_quality_score": 0.4, "retry_count": 3, "query": "reformulated-2"},
    ])

    async def mock_router(s):
        call_order.append("router")
        return {"classification": "simple"}

    async def mock_planner(s):
        call_order.append("planner")
        return {"plan": []}

    async def mock_executor(s):
        call_order.append("executor")
        return {"tool_results": {}, "retrieved_chunks": [], "reranked_chunks": []}

    async def mock_evaluator(s):
        call_order.append("evaluator")
        return next(evaluator_returns)

    async def mock_synthesizer(s):
        call_order.append("synthesizer")
        return {"final_output": "best effort answer"}

    graph = _build_test_graph(mock_router, mock_planner, mock_executor, mock_evaluator, mock_synthesizer)
    await graph.ainvoke(state)

    assert call_order == [
        "router", "executor", "evaluator",
        "executor", "evaluator",
        "executor", "evaluator",
        "synthesizer",
    ]
    assert call_order.count("executor") == 3
    assert call_order.count("evaluator") == 3


async def test_graph_retry_score_improves():
    call_order: list[str] = []
    state = dict(_BASE_STATE)

    evaluator_returns = iter([
        {"retrieval_quality_score": 0.3, "retry_count": 1, "query": "r1"},
        {"retrieval_quality_score": 0.3, "retry_count": 2, "query": "r2"},
        {"retrieval_quality_score": 0.85, "retry_count": 2, "query": "r2"},
    ])

    async def mock_router(s):
        call_order.append("router")
        return {"classification": "simple"}

    async def mock_planner(s):
        call_order.append("planner")
        return {"plan": []}

    async def mock_executor(s):
        call_order.append("executor")
        return {"tool_results": {}, "retrieved_chunks": [], "reranked_chunks": []}

    async def mock_evaluator(s):
        call_order.append("evaluator")
        return next(evaluator_returns)

    async def mock_synthesizer(s):
        call_order.append("synthesizer")
        return {"final_output": "good answer"}

    graph = _build_test_graph(mock_router, mock_planner, mock_executor, mock_evaluator, mock_synthesizer)
    await graph.ainvoke(state)

    assert call_order == [
        "router", "executor", "evaluator",
        "executor", "evaluator",
        "executor", "evaluator",
        "synthesizer",
    ]
    assert call_order.count("synthesizer") == 1
