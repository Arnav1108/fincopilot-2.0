from __future__ import annotations

from langgraph.graph import END

from app.agent.graph import route_after_evaluator, route_after_router


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
