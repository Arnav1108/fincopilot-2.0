from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from app.agent.evaluator import evaluator_node
from app.agent.executor import executor_node
from app.agent.planner import planner_node
from app.agent.router import router_node
from app.agent.synthesizer import synthesizer_node
from app.tools import ToolError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_router_state(**overrides):
    base = {
        "query": "What is Apple revenue?",
        "conversation_summary": "",
        "recent_messages": [],
    }
    return {**base, **overrides}


def _make_planner_state(**overrides):
    base = {
        "classification": "complex",
        "query": "Compare Apple and Microsoft revenue",
    }
    return {**base, **overrides}


def _make_executor_state(**overrides):
    base = {
        "classification": "simple",
        "query": "What is Apple revenue?",
        "user_id": "user-123",
        "tool_results": {},
        "plan": [],
    }
    return {**base, **overrides}


def _make_evaluator_state(**overrides):
    base = {
        "reranked_chunks": [
            {"content": "Apple revenue was 100B", "metadata": {}, "score": 0.9}
        ],
        "retry_count": 0,
        "query": "What is Apple revenue?",
    }
    return {**base, **overrides}


def _make_synthesizer_state(**overrides):
    base = {
        "reranked_chunks": [
            {"content": "Apple revenue was 100B", "metadata": {}, "score": 0.9}
        ],
        "query": "What is Apple revenue?",
        "model": "gpt-4o",
        "conversation_summary": "",
        "recent_messages": [],
        "analyst_profile": {},
    }
    return {**base, **overrides}


def _mock_openai_client(content: str, module: str):
    """Return (patcher, mock_client). mock_client.chat.completions.create returns content."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    patcher = patch(module, return_value=mock_client)
    return patcher, mock_client


# ---------------------------------------------------------------------------
# ROUTER (5 tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_simple():
    patcher, _ = _mock_openai_client("simple", "app.agent.router.openai.AsyncOpenAI")
    with patcher:
        result = await router_node(_make_router_state())
    assert result["classification"] == "simple"


@pytest.mark.asyncio
async def test_router_complex():
    patcher, _ = _mock_openai_client("complex", "app.agent.router.openai.AsyncOpenAI")
    with patcher:
        result = await router_node(_make_router_state())
    assert result["classification"] == "complex"


@pytest.mark.asyncio
async def test_router_ingest():
    patcher, _ = _mock_openai_client("ingest", "app.agent.router.openai.AsyncOpenAI")
    with patcher:
        result = await router_node(_make_router_state())
    assert result["classification"] == "ingest"


@pytest.mark.asyncio
async def test_router_unknown_falls_back_to_complex():
    patcher, _ = _mock_openai_client("gibberish", "app.agent.router.openai.AsyncOpenAI")
    with patcher:
        result = await router_node(_make_router_state())
    assert result["classification"] == "complex"
    assert "error" not in result


@pytest.mark.asyncio
async def test_router_openai_error():
    with patch("app.agent.router.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("api error"))
        MockOpenAI.return_value = mock_client
        result = await router_node(_make_router_state())
    assert "error" in result


# ---------------------------------------------------------------------------
# PLANNER (5 tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_planner_non_complex_short_circuits():
    with patch("app.agent.planner.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        MockOpenAI.return_value = mock_client

        state = _make_planner_state(classification="simple")
        result = await planner_node(state)

    assert result["plan"] == []
    mock_client.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_planner_valid_plan():
    steps = [
        {"id": "step_1", "tool_name": "financial_data", "input_template": "AAPL revenue", "dependencies": []},
        {"id": "step_2", "tool_name": "news_fetch", "input_template": "Apple news", "dependencies": []},
    ]
    content = json.dumps({"steps": steps})
    patcher, _ = _mock_openai_client(content, "app.agent.planner.openai.AsyncOpenAI")
    with patcher:
        result = await planner_node(_make_planner_state())
    assert len(result["plan"]) == 2
    assert result["plan"][0]["tool_name"] == "financial_data"
    assert result["plan"][1]["tool_name"] == "news_fetch"


@pytest.mark.asyncio
async def test_planner_invalid_tool_dropped():
    steps = [
        {"id": "step_1", "tool_name": "nonexistent_tool", "input_template": "bad", "dependencies": []},
        {"id": "step_2", "tool_name": "financial_data", "input_template": "AAPL revenue", "dependencies": []},
    ]
    content = json.dumps({"steps": steps})
    patcher, _ = _mock_openai_client(content, "app.agent.planner.openai.AsyncOpenAI")
    with patcher:
        result = await planner_node(_make_planner_state())
    assert len(result["plan"]) == 1
    assert result["plan"][0]["id"] == "step_2"


@pytest.mark.asyncio
async def test_planner_single_dict_wrapped():
    single_step = {
        "id": "step_1",
        "tool_name": "financial_data",
        "input_template": "AAPL revenue",
        "dependencies": [],
    }
    content = json.dumps(single_step)
    patcher, _ = _mock_openai_client(content, "app.agent.planner.openai.AsyncOpenAI")
    with patcher:
        result = await planner_node(_make_planner_state())
    assert len(result["plan"]) == 1
    assert result["plan"][0]["id"] == "step_1"


@pytest.mark.asyncio
async def test_planner_openai_error():
    with patch("app.agent.planner.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("api error"))
        MockOpenAI.return_value = mock_client
        result = await planner_node(_make_planner_state())
    assert "error" in result


# ---------------------------------------------------------------------------
# EXECUTOR (5 tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_executor_simple_calls_retrieval():
    mock_chunks = [{"content": "Apple revenue 100B", "metadata": {}, "score": 0.9}]
    mock_tool = AsyncMock(return_value=mock_chunks)

    with patch("app.agent.executor.TOOL_REGISTRY", {"document_retrieval": mock_tool}):
        result = await executor_node(_make_executor_state(classification="simple"))

    mock_tool.assert_called_once()
    assert len(result["retrieved_chunks"]) == 1
    assert result["retrieved_chunks"][0]["content"] == "Apple revenue 100B"


@pytest.mark.asyncio
async def test_executor_complex_parallel_steps():
    plan = [
        {"id": "step_1", "tool_name": "financial_data", "input_template": "AAPL revenue", "dependencies": []},
        {"id": "step_2", "tool_name": "news_fetch", "input_template": "Apple news", "dependencies": []},
    ]
    mock_financial = AsyncMock(return_value={"revenue": "100B"})
    mock_news = AsyncMock(return_value={"articles": ["article1"]})

    with patch("app.agent.executor.TOOL_REGISTRY", {
        "financial_data": mock_financial,
        "news_fetch": mock_news,
    }):
        result = await executor_node(_make_executor_state(classification="complex", plan=plan))

    mock_financial.assert_called_once()
    mock_news.assert_called_once()
    assert "step_1" in result["tool_results"]
    assert "step_2" in result["tool_results"]


@pytest.mark.asyncio
async def test_executor_complex_dependency_chain():
    plan = [
        {"id": "step_1", "tool_name": "financial_data", "input_template": "AAPL revenue", "dependencies": []},
        {"id": "step_2", "tool_name": "financial_calculator", "input_template": "calculate: {step_1}", "dependencies": ["step_1"]},
    ]
    mock_financial = AsyncMock(return_value={"revenue": "100B"})
    mock_calculator = AsyncMock(return_value={"result": "42"})

    with patch("app.agent.executor.TOOL_REGISTRY", {
        "financial_data": mock_financial,
        "financial_calculator": mock_calculator,
    }):
        result = await executor_node(_make_executor_state(classification="complex", plan=plan))

    assert "step_1" in result["tool_results"]
    assert "step_2" in result["tool_results"]
    mock_financial.assert_called_once()
    mock_calculator.assert_called_once()


@pytest.mark.asyncio
async def test_executor_tool_error_isolated():
    plan = [
        {"id": "step_1", "tool_name": "financial_data", "input_template": "AAPL revenue", "dependencies": []},
        {"id": "step_2", "tool_name": "news_fetch", "input_template": "Apple news", "dependencies": []},
    ]
    mock_failing = AsyncMock(side_effect=ToolError("upstream failure"))
    mock_succeeding = AsyncMock(return_value={"articles": ["article1"]})

    with patch("app.agent.executor.TOOL_REGISTRY", {
        "financial_data": mock_failing,
        "news_fetch": mock_succeeding,
    }):
        result = await executor_node(_make_executor_state(classification="complex", plan=plan))

    assert "error" in result["tool_results"]["step_1"]
    assert result["tool_results"]["step_2"] == {"articles": ["article1"]}


@pytest.mark.asyncio
async def test_executor_reranked_equals_retrieved():
    mock_chunks = [{"content": "Revenue data", "metadata": {}, "score": 0.8}]
    mock_tool = AsyncMock(return_value=mock_chunks)

    with patch("app.agent.executor.TOOL_REGISTRY", {"document_retrieval": mock_tool}):
        result = await executor_node(_make_executor_state(classification="simple"))

    assert result["reranked_chunks"] == result["retrieved_chunks"]


# ---------------------------------------------------------------------------
# EVALUATOR (5 tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluator_empty_chunks_short_circuits():
    with patch("app.agent.evaluator.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        MockOpenAI.return_value = mock_client

        state = _make_evaluator_state(reranked_chunks=[])
        result = await evaluator_node(state)

    assert result["retrieval_quality_score"] == 0.0
    mock_client.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_evaluator_high_score_no_retry():
    patcher, mock_client = _mock_openai_client("0.85", "app.agent.evaluator.openai.AsyncOpenAI")
    with patcher:
        result = await evaluator_node(_make_evaluator_state())
    assert result["retrieval_quality_score"] == pytest.approx(0.85)
    assert result["retry_count"] == 0
    assert result["query"] == "What is Apple revenue?"
    mock_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_evaluator_low_score_triggers_retry():
    score_response = MagicMock()
    score_response.choices = [MagicMock()]
    score_response.choices[0].message.content = "0.3"

    reformulate_response = MagicMock()
    reformulate_response.choices = [MagicMock()]
    reformulate_response.choices[0].message.content = "Apple Inc annual revenue breakdown"

    with patch("app.agent.evaluator.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[score_response, reformulate_response]
        )
        MockOpenAI.return_value = mock_client

        result = await evaluator_node(_make_evaluator_state(retry_count=0))

    assert result["retry_count"] == 1
    assert result["query"] == "Apple Inc annual revenue breakdown"
    assert mock_client.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_evaluator_retry_cap_at_3():
    patcher, mock_client = _mock_openai_client("0.3", "app.agent.evaluator.openai.AsyncOpenAI")
    with patcher:
        result = await evaluator_node(_make_evaluator_state(retry_count=3))
    assert result["retry_count"] == 3
    assert result["query"] == "What is Apple revenue?"
    mock_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_evaluator_unparseable_score_clamped():
    patcher, _ = _mock_openai_client("not_a_float", "app.agent.evaluator.openai.AsyncOpenAI")
    with patcher:
        result = await evaluator_node(_make_evaluator_state())
    assert "error" not in result
    score = result["retrieval_quality_score"]
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# SYNTHESIZER (5 tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_empty_chunks_short_circuits():
    with patch("app.agent.synthesizer.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        MockOpenAI.return_value = mock_client

        result = await synthesizer_node(_make_synthesizer_state(reranked_chunks=[]))

    assert result["final_output"] == "No relevant documents were found to answer this query."
    mock_client.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_synthesizer_collects_stream():
    async def fake_stream(*args, **kwargs):
        for content in ["Hello", " world", "!"]:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = content
            yield chunk

    with patch("app.agent.synthesizer.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_create = AsyncMock(return_value=fake_stream())
        mock_client.chat.completions.create = mock_create
        MockOpenAI.return_value = mock_client

        result = await synthesizer_node(_make_synthesizer_state())

    assert result["final_output"] == "Hello world!"


@pytest.mark.asyncio
async def test_synthesizer_default_model():
    async def fake_stream(*args, **kwargs):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "answer"
        yield chunk

    with patch("app.agent.synthesizer.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_create = AsyncMock(return_value=fake_stream())
        mock_client.chat.completions.create = mock_create
        MockOpenAI.return_value = mock_client

        await synthesizer_node(_make_synthesizer_state(model=""))

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_synthesizer_no_advice_in_system_prompt():
    async def fake_stream(*args, **kwargs):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = "result"
        yield chunk

    with patch("app.agent.synthesizer.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_create = AsyncMock(return_value=fake_stream())
        mock_client.chat.completions.create = mock_create
        MockOpenAI.return_value = mock_client

        await synthesizer_node(_make_synthesizer_state())

    messages = mock_create.call_args.kwargs["messages"]
    system_content = next(m["content"] for m in messages if m["role"] == "system")
    lowered = system_content.lower()
    assert "buy" in lowered or "sell" in lowered or "hold" in lowered
    assert "financial advice" in lowered or "not constitute" in lowered


@pytest.mark.asyncio
async def test_synthesizer_openai_error():
    with patch("app.agent.synthesizer.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("api error"))
        MockOpenAI.return_value = mock_client

        result = await synthesizer_node(_make_synthesizer_state())

    assert "error" in result
