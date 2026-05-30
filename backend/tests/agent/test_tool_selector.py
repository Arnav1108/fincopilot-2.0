from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.tool_selector import tool_selector_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides):
    return {
        "classification": "simple",
        "query": "What is Apple's stock price?",
        "user_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "has_uploaded_documents": False,
        **overrides,
    }


def _mock_openai(content: str):
    """Return (patcher, mock_client) that makes AsyncOpenAI return `content`."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    patcher = patch("app.agent.tool_selector.openai_client", new=mock_client)
    return patcher, mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_selects_financial_data():
    payload = json.dumps({"tool_name": "financial_data", "input": {"ticker": "AAPL"}})
    patcher, _ = _mock_openai(payload)
    with patcher:
        result = await tool_selector_node(_make_state())

    assert len(result["plan"]) == 1
    assert result["plan"][0]["tool_name"] == "financial_data"
    assert result["plan"][0]["input"]["ticker"] == "AAPL"


async def test_selects_document_retrieval_when_has_documents():
    payload = json.dumps({"tool_name": "document_retrieval", "input": {"query": "risks"}})
    patcher, _ = _mock_openai(payload)
    with patcher:
        result = await tool_selector_node(_make_state(has_uploaded_documents=True))

    assert len(result["plan"]) == 1
    step = result["plan"][0]
    assert step["tool_name"] == "document_retrieval"
    # user_id and conversation_id must have been injected
    assert "user_id" in step["input"]
    assert "conversation_id" in step["input"]


async def test_null_tool_name_returns_empty_plan():
    payload = json.dumps({"tool_name": None, "input": {}})
    patcher, _ = _mock_openai(payload)
    with patcher:
        result = await tool_selector_node(_make_state())

    assert result["plan"] == []


async def test_invalid_tool_name_returns_empty_plan():
    """financial_calculator has been removed from the registry."""
    payload = json.dumps({"tool_name": "financial_calculator", "input": {}})
    patcher, _ = _mock_openai(payload)
    with patcher:
        result = await tool_selector_node(_make_state())

    assert result["plan"] == []
    assert "error" not in result


async def test_validation_error_returns_empty_plan():
    """financial_data ticker must match ^[A-Z0-9]{1,10}$; lowercase fails."""
    payload = json.dumps({"tool_name": "financial_data", "input": {"ticker": "aapl"}})
    patcher, _ = _mock_openai(payload)
    with patcher:
        result = await tool_selector_node(_make_state())

    assert result["plan"] == []
    assert "error" not in result


async def test_non_simple_classification_returns_empty_plan():
    """tool_selector must short-circuit without calling OpenAI for non-simple."""
    with patch("app.agent.tool_selector.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock()

        result = await tool_selector_node(_make_state(classification="complex"))

    assert result["plan"] == []
    mock_client.chat.completions.create.assert_not_called()


async def test_openai_exception_returns_error():
    with patch("app.agent.tool_selector.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("api error"))

        result = await tool_selector_node(_make_state())

    assert "error" in result
    assert "api error" in result["error"]
