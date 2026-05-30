from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.synthesizer import _extract_chart_data, synthesizer_node
import structlog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides):
    return {
        "query": "Show me Apple's revenue for the last 4 quarters",
        "model": "gpt-4o",
        "reranked_chunks": [],
        "tool_results": {},
        "conversation_summary": "",
        "recent_messages": [],
        "analyst_profile": {},
        **overrides,
    }


def _ok_envelope(tool_name: str = "financial_data", data: dict | None = None) -> dict:
    return {
        "tool_name": tool_name,
        "status": "ok",
        "data": data or {"quarterly_revenue": [
            {"quarter": "Q1 2024", "revenue": 119.6},
            {"quarter": "Q2 2024", "revenue": 85.8},
            {"quarter": "Q3 2024", "revenue": 94.9},
            {"quarter": "Q4 2024", "revenue": 124.3},
        ]},
    }


def _make_json_response(payload: dict) -> MagicMock:
    """Build a mock non-streaming completion response with a JSON body."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(payload)
    return resp


def _bar_chart_payload() -> dict:
    return {
        "chart_type": "bar",
        "title": "Apple Quarterly Revenue",
        "x_axis_label": "Quarter",
        "y_axis_label": "Revenue (B)",
        "series": [
            {
                "name": "Revenue",
                "data": [
                    {"x": "Q1 2024", "y": 119.6},
                    {"x": "Q2 2024", "y": 85.8},
                    {"x": "Q3 2024", "y": 94.9},
                    {"x": "Q4 2024", "y": 124.3},
                ],
            }
        ],
    }


async def _fake_text_stream(tokens: list[str] = None):
    """Async generator mimicking the streaming text response."""
    for tok in (tokens or ["Revenue was strong."]):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = tok
        yield chunk


# ---------------------------------------------------------------------------
# _extract_chart_data unit tests
# ---------------------------------------------------------------------------

async def test_extract_chart_data_returns_bar_payload():
    """Successful extraction returns the validated chart dict."""
    logger = structlog.get_logger()
    successful = {"step_0": _ok_envelope()}
    json_resp = _make_json_response(_bar_chart_payload())

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(return_value=json_resp)
        result = await _extract_chart_data(successful, "gpt-4o", logger)

    assert result is not None
    assert result["chart_type"] == "bar"
    assert result["title"] == "Apple Quarterly Revenue"
    assert len(result["series"]) == 1
    assert result["series"][0]["name"] == "Revenue"


async def test_extract_chart_data_returns_none_when_chart_type_null():
    """GPT-4o returning chart_type=null must produce None."""
    logger = structlog.get_logger()
    successful = {"step_0": _ok_envelope()}
    json_resp = _make_json_response({"chart_type": None})

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(return_value=json_resp)
        result = await _extract_chart_data(successful, "gpt-4o", logger)

    assert result is None


async def test_extract_chart_data_returns_none_when_field_missing():
    """Payload missing required fields must return None (not raise)."""
    logger = structlog.get_logger()
    successful = {"step_0": _ok_envelope()}
    # Missing x_axis_label, y_axis_label, series
    json_resp = _make_json_response({"chart_type": "bar", "title": "Apple Revenue"})

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(return_value=json_resp)
        result = await _extract_chart_data(successful, "gpt-4o", logger)

    assert result is None


async def test_extract_chart_data_returns_none_on_exception():
    """Any exception from OpenAI must be swallowed and return None."""
    logger = structlog.get_logger()
    successful = {"step_0": _ok_envelope()}

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        result = await _extract_chart_data(successful, "gpt-4o", logger)

    assert result is None


async def test_extract_chart_data_uses_full_untruncated_data():
    """The extraction call must include the full tool data, not truncated text."""
    logger = structlog.get_logger()
    large_data = {"values": list(range(200))}  # would exceed 500-char truncation
    successful = {"step_0": _ok_envelope(data=large_data)}
    json_resp = _make_json_response({"chart_type": None})

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(return_value=json_resp)
        await _extract_chart_data(successful, "gpt-4o", logger)

    call_kwargs = mock_client.chat.completions.create.call_args
    user_content = next(
        m["content"]
        for m in call_kwargs.kwargs["messages"]
        if m["role"] == "user"
    )
    # All 200 values must appear in the extraction prompt
    assert '"values"' in user_content
    assert "199" in user_content


# ---------------------------------------------------------------------------
# synthesizer_node integration tests
# ---------------------------------------------------------------------------

async def test_synthesizer_node_skips_chart_for_rag():
    """RAG path (chunks present, no tools) must skip chart extraction entirely."""
    chunk = {"content": "Revenue was $100B", "metadata": {}, "score": 0.9}
    state = _make_state(reranked_chunks=[chunk], tool_results={})

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=_fake_text_stream()
        )
        result = await synthesizer_node(state)

    assert result.get("chart_data") is None
    # Only one call — the streaming text call; no second extraction call
    assert mock_client.chat.completions.create.call_count == 1


async def test_synthesizer_node_skips_chart_for_llm_only():
    """LLM-only path (no chunks, no tools) must skip chart extraction."""
    state = _make_state(
        reranked_chunks=[],
        tool_results={},
        query="What is a P/E ratio?",
    )

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            return_value=_fake_text_stream()
        )
        result = await synthesizer_node(state)

    assert result.get("chart_data") is None
    assert mock_client.chat.completions.create.call_count == 1


async def test_synthesizer_node_includes_chart_data_for_tool_results():
    """Tool-result path must call extraction and return chart_data in state."""
    state = _make_state(
        reranked_chunks=[],
        tool_results={"step_0": _ok_envelope()},
    )

    json_resp = _make_json_response(_bar_chart_payload())

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        # First call: streaming text; second call: JSON extraction
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[_fake_text_stream(), json_resp]
        )
        result = await synthesizer_node(state)

    assert result.get("chart_data") is not None
    assert result["chart_data"]["chart_type"] == "bar"
    # Exactly two OpenAI calls made
    assert mock_client.chat.completions.create.call_count == 2


async def test_synthesizer_node_chart_data_none_when_extraction_fails():
    """If chart extraction raises, chart_data must be None and text still returned."""
    state = _make_state(
        reranked_chunks=[],
        tool_results={"step_0": _ok_envelope()},
    )

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[_fake_text_stream(), Exception("OpenAI timeout")]
        )
        result = await synthesizer_node(state)

    assert result.get("chart_data") is None
    assert "Revenue was strong." in result.get("final_output", "")


async def test_synthesizer_node_chart_data_none_when_type_is_null():
    """If GPT-4o decides data is not chartable, chart_data must be None."""
    state = _make_state(
        reranked_chunks=[],
        tool_results={"step_0": _ok_envelope()},
    )

    json_resp = _make_json_response({"chart_type": None})

    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[_fake_text_stream(), json_resp]
        )
        result = await synthesizer_node(state)

    assert result.get("chart_data") is None
    assert result.get("final_output") == "Revenue was strong."
