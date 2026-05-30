from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.executor import executor_node
from app.tools import ToolError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides):
    return {
        "query": "What is Apple revenue?",
        "user_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "plan": [],
        "tool_results": {},
        **overrides,
    }


def _ok_result(data: dict | None = None) -> MagicMock:
    """A mock tool return value with model_dump returning a real dict."""
    m = MagicMock()
    m.model_dump.return_value = data or {"ticker": "AAPL", "current_price": 150.0}
    return m


def _retrieval_result(chunks_content: list[str]) -> MagicMock:
    """A mock DocumentRetrievalOutput with .chunks attribute."""
    chunks = []
    for text in chunks_content:
        c = MagicMock()
        c.content = text
        c.metadata = {}
        c.chunk_id = uuid.uuid4()
        c.document_id = uuid.uuid4()
        c.similarity_score = 0.9
        chunks.append(c)
    m = MagicMock()
    m.chunks = chunks
    m.model_dump.return_value = {"chunks": []}
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_empty_plan_returns_empty_results():
    result = await executor_node(_make_state(plan=[]))
    assert result["tool_results"] == {}
    assert result["retrieved_chunks"] == []
    assert result["rag_used"] is False


async def test_successful_tool_call():
    plan = [{"tool_name": "financial_data", "input": {"ticker": "AAPL"}}]
    mock_tool = AsyncMock(return_value=_ok_result({"ticker": "AAPL", "current_price": 150.0}))

    with patch("app.agent.executor.TOOL_REGISTRY", {"financial_data": mock_tool}):
        result = await executor_node(_make_state(plan=plan))

    assert "step_0" in result["tool_results"]
    envelope = result["tool_results"]["step_0"]
    assert envelope["status"] == "ok"
    assert envelope["tool_name"] == "financial_data"
    assert isinstance(envelope["data"], dict)


async def test_tool_error_isolated():
    """A ToolError on step_0 must not prevent step_1 from succeeding."""
    plan = [
        {"tool_name": "financial_data", "input": {"ticker": "AAPL"}},
        {"tool_name": "web_search", "input": {"query": "Apple news", "search_type": "news"}},
    ]
    mock_failing = AsyncMock(side_effect=ToolError("upstream failure"))
    mock_ok = AsyncMock(return_value=_ok_result({"results": []}))

    with patch(
        "app.agent.executor.TOOL_REGISTRY",
        {"financial_data": mock_failing, "web_search": mock_ok},
    ):
        result = await executor_node(_make_state(plan=plan))

    assert result["tool_results"]["step_0"]["status"] == "error"
    assert "upstream failure" in result["tool_results"]["step_0"]["error"]
    assert result["tool_results"]["step_1"]["status"] == "ok"


async def test_validation_error_isolated():
    """Invalid input on step_0 (bad ticker pattern) must not prevent step_1."""
    plan = [
        {"tool_name": "financial_data", "input": {"ticker": "aapl lowercase"}},  # fails pattern
        {"tool_name": "web_search", "input": {"query": "Apple news", "search_type": "news"}},
    ]
    mock_web = AsyncMock(return_value=_ok_result({"results": []}))

    with patch(
        "app.agent.executor.TOOL_REGISTRY",
        {"financial_data": AsyncMock(), "web_search": mock_web},
    ):
        result = await executor_node(_make_state(plan=plan))

    assert result["tool_results"]["step_0"]["status"] == "error"
    assert result["tool_results"]["step_1"]["status"] == "ok"


async def test_generic_exception_isolated():
    """A bare Exception on step_0 must not prevent step_1."""
    plan = [
        {"tool_name": "financial_data", "input": {"ticker": "AAPL"}},
        {"tool_name": "web_search", "input": {"query": "Apple news", "search_type": "news"}},
    ]
    mock_failing = AsyncMock(side_effect=Exception("network timeout"))
    mock_ok = AsyncMock(return_value=_ok_result({"results": []}))

    with patch(
        "app.agent.executor.TOOL_REGISTRY",
        {"financial_data": mock_failing, "web_search": mock_ok},
    ):
        result = await executor_node(_make_state(plan=plan))

    assert result["tool_results"]["step_0"]["status"] == "error"
    assert "network timeout" in result["tool_results"]["step_0"]["error"]
    assert result["tool_results"]["step_1"]["status"] == "ok"


async def test_document_retrieval_chunk_collection():
    """document_retrieval result must be normalised into retrieved_chunks and rag_used=True."""
    plan = [{"tool_name": "document_retrieval", "input": {"query": "revenue"}}]
    mock_retrieval = AsyncMock(return_value=_retrieval_result(["Apple revenue was 100B"]))

    with patch("app.agent.executor.TOOL_REGISTRY", {"document_retrieval": mock_retrieval}):
        result = await executor_node(_make_state(plan=plan))

    assert result["rag_used"] is True
    assert len(result["retrieved_chunks"]) == 1
    assert result["retrieved_chunks"][0]["content"] == "Apple revenue was 100B"


async def test_document_retrieval_runs_first():
    """document_retrieval must run before other tools regardless of plan order."""
    call_order: list[str] = []

    plan = [
        {"tool_name": "web_search", "input": {"query": "Apple news", "search_type": "news"}},
        {"tool_name": "document_retrieval", "input": {"query": "revenue"}},
    ]

    async def mock_retrieval(inp):
        call_order.append("document_retrieval")
        return _retrieval_result([])

    async def mock_web(inp):
        call_order.append("web_search")
        return _ok_result({"results": []})

    # model_dump must be available on the web result
    mock_web_tool = AsyncMock(side_effect=mock_web)
    mock_web_tool.return_value = _ok_result({"results": []})

    with patch(
        "app.agent.executor.TOOL_REGISTRY",
        {"document_retrieval": AsyncMock(side_effect=mock_retrieval), "web_search": AsyncMock(side_effect=mock_web)},
    ):
        await executor_node(_make_state(plan=plan))

    assert call_order.index("document_retrieval") < call_order.index("web_search")
