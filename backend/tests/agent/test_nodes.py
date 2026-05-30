from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.router import router_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_router_state(**overrides):
    base = {
        "query": "What is Apple revenue?",
        "conversation_summary": "",
        "recent_messages": [],
        "has_uploaded_documents": False,
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

async def test_router_simple():
    patcher, _ = _mock_openai_client("simple", "app.agent.router.openai.AsyncOpenAI")
    with patcher:
        result = await router_node(_make_router_state())
    assert result["classification"] == "simple"


async def test_router_complex():
    patcher, _ = _mock_openai_client("complex", "app.agent.router.openai.AsyncOpenAI")
    with patcher:
        result = await router_node(_make_router_state())
    assert result["classification"] == "complex"


async def test_router_ingest():
    patcher, _ = _mock_openai_client("ingest", "app.agent.router.openai.AsyncOpenAI")
    with patcher:
        result = await router_node(_make_router_state())
    assert result["classification"] == "ingest"


async def test_router_unknown_falls_back_to_complex():
    patcher, _ = _mock_openai_client("gibberish", "app.agent.router.openai.AsyncOpenAI")
    with patcher:
        result = await router_node(_make_router_state())
    assert result["classification"] == "complex"
    assert "error" not in result


async def test_router_openai_error():
    with patch("app.agent.router.openai.AsyncOpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("api error"))
        MockOpenAI.return_value = mock_client
        result = await router_node(_make_router_state())
    assert "error" in result
