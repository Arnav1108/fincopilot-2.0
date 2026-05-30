from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.planner import planner_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides):
    return {
        "classification": "complex",
        "query": "Compare Apple and Microsoft revenue",
        "has_uploaded_documents": False,
        **overrides,
    }


def _mock_openai(content: str):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    patcher = patch("app.agent.planner.openai_client", new=mock_client)
    return patcher, mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_flat_list_output():
    """Planner must return a flat list of PlanStep dicts with tool_name and input."""
    steps = [
        {"tool_name": "financial_data", "input": {"ticker": "AAPL"}},
        {"tool_name": "web_search", "input": {"query": "Apple news", "search_type": "news"}},
    ]
    patcher, _ = _mock_openai(json.dumps({"steps": steps}))
    with patcher:
        result = await planner_node(_make_state())

    assert len(result["plan"]) == 2
    assert result["plan"][0]["tool_name"] == "financial_data"
    assert result["plan"][0]["input"] == {"ticker": "AAPL"}
    assert result["plan"][1]["tool_name"] == "web_search"
    # Verify the NEW flat shape: no id, no dependencies, no input_template
    assert "id" not in result["plan"][0]
    assert "dependencies" not in result["plan"][0]
    assert "input_template" not in result["plan"][0]


async def test_unknown_tool_dropped():
    """Steps with tool_name not in TOOL_REGISTRY must be silently dropped."""
    steps = [
        {"tool_name": "nonexistent_tool", "input": {}},
        {"tool_name": "financial_data", "input": {"ticker": "MSFT"}},
    ]
    patcher, _ = _mock_openai(json.dumps({"steps": steps}))
    with patcher:
        result = await planner_node(_make_state())

    assert len(result["plan"]) == 1
    assert result["plan"][0]["tool_name"] == "financial_data"


async def test_financial_calculator_rejected():
    """financial_calculator must be dropped because it is not in the registry."""
    steps = [
        {"tool_name": "financial_calculator", "input": {"expression": "100 * 0.1"}},
        {"tool_name": "web_search", "input": {"query": "market news", "search_type": "news"}},
    ]
    patcher, _ = _mock_openai(json.dumps({"steps": steps}))
    with patcher:
        result = await planner_node(_make_state())

    assert all(s["tool_name"] != "financial_calculator" for s in result["plan"])
    assert len(result["plan"]) == 1
    assert result["plan"][0]["tool_name"] == "web_search"


async def test_six_step_cap():
    """Plans with more than 6 steps must be truncated to 6."""
    steps = [
        {"tool_name": "web_search", "input": {"query": f"query {i}", "search_type": "news"}}
        for i in range(8)
    ]
    patcher, _ = _mock_openai(json.dumps({"steps": steps}))
    with patcher:
        result = await planner_node(_make_state())

    assert len(result["plan"]) == 6


async def test_parse_failure_returns_empty_plan():
    """Unrecognisable JSON shape (no steps key, no list) must yield empty plan, not error."""
    patcher, _ = _mock_openai(json.dumps({"unexpected_key": "unexpected_value"}))
    with patcher:
        result = await planner_node(_make_state())

    assert result.get("plan") == []
    assert "error" not in result


async def test_non_complex_returns_empty_plan():
    """Non-complex classification must short-circuit without calling OpenAI."""
    with patch("app.agent.planner.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock()

        result = await planner_node(_make_state(classification="simple"))

    assert result["plan"] == []
    mock_client.chat.completions.create.assert_not_called()
