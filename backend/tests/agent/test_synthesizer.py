from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.synthesizer import (
    _SYSTEM_PROMPT_LLM_ONLY,
    _SYSTEM_PROMPT_RAG,
    _SYSTEM_PROMPT_TOOLS,
    synthesizer_node,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides):
    return {
        "query": "What is Apple revenue?",
        "model": "gpt-4o",
        "reranked_chunks": [],
        "tool_results": {},
        "conversation_summary": "",
        "recent_messages": [],
        "analyst_profile": {},
        **overrides,
    }


def _chunk(text: str = "Apple revenue was 100B") -> dict:
    return {"content": text, "metadata": {}, "score": 0.9}


def _ok_envelope(tool_name: str = "financial_data", data: dict | None = None) -> dict:
    return {"tool_name": tool_name, "status": "ok", "data": data or {"current_price": 150.0}}


def _err_envelope(tool_name: str = "financial_data", error: str = "API error") -> dict:
    return {"tool_name": tool_name, "status": "error", "error": error}


async def _run_with_stream_mock(state: dict, tokens: list[str] = None) -> tuple[dict, MagicMock]:
    """Run synthesizer_node with a streaming mock; return (result, mock_create).

    Provides two side-effect values so tool-result paths (which make a second
    non-streaming chart-extraction call) don't exhaust the mock.
    """
    if tokens is None:
        tokens = ["answer"]

    async def fake_stream():
        for tok in tokens:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = tok
            yield chunk

    # Null-chart response consumed by the extraction call on tool-result paths
    null_chart_resp = MagicMock()
    null_chart_resp.choices = [MagicMock()]
    null_chart_resp.choices[0].message.content = '{"chart_type": null}'

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[fake_stream(), null_chart_resp]
    )

    with patch("app.agent.synthesizer.openai_client", mock_client):
        result = await synthesizer_node(state)

    return result, mock_client.chat.completions.create


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_renders_successful_tool_envelopes():
    """Both successful tool envelopes must appear in the prompt sent to OpenAI."""
    tool_results = {
        "step_0": _ok_envelope("financial_data", {"current_price": 150.0}),
        "step_1": _ok_envelope("web_search", {"results": []}),
    }
    state = _make_state(tool_results=tool_results)
    _, mock_create = await _run_with_stream_mock(state)

    # call_args_list[0] = text synthesis call; [1] = chart extraction call
    messages = mock_create.call_args_list[0].kwargs["messages"]
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "financial_data result:" in user_content
    assert "web_search result:" in user_content


async def test_renders_failure_lines():
    """Failed envelope must appear as 'tool_name failed: reason' in the prompt."""
    tool_results = {"step_0": _err_envelope("financial_data", "API error")}
    state = _make_state(tool_results=tool_results)
    _, mock_create = await _run_with_stream_mock(state)

    messages = mock_create.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "financial_data failed: API error" in user_content


async def test_no_docs_guard_fires():
    """No chunks + no successful tools + document-specific query → canned message, no OpenAI call."""
    with patch("app.agent.synthesizer.openai_client") as mock_client:
        mock_client.chat.completions.create = AsyncMock()
        result = await synthesizer_node(_make_state(query="summarize the document"))

    assert "No documents uploaded yet" in result["final_output"]
    mock_client.chat.completions.create.assert_not_called()


async def test_rag_prompt_selected_when_chunks_present():
    """Non-empty reranked_chunks must trigger _SYSTEM_PROMPT_RAG."""
    state = _make_state(reranked_chunks=[_chunk()])
    _, mock_create = await _run_with_stream_mock(state)

    messages = mock_create.call_args.kwargs["messages"]
    system_content = next(m["content"] for m in messages if m["role"] == "system")
    assert system_content == _SYSTEM_PROMPT_RAG


async def test_tools_prompt_selected_when_no_chunks():
    """No chunks but successful tools must trigger _SYSTEM_PROMPT_TOOLS."""
    tool_results = {"step_0": _ok_envelope("financial_data")}
    state = _make_state(tool_results=tool_results, reranked_chunks=[])
    _, mock_create = await _run_with_stream_mock(state)

    # call_args_list[0] = text synthesis call; [1] = chart extraction call
    messages = mock_create.call_args_list[0].kwargs["messages"]
    system_content = next(m["content"] for m in messages if m["role"] == "system")
    assert system_content == _SYSTEM_PROMPT_TOOLS


async def test_llm_only_prompt_selected_when_nothing():
    """No chunks and no tool results must trigger _SYSTEM_PROMPT_LLM_ONLY."""
    state = _make_state(reranked_chunks=[], tool_results={}, query="What is a P/E ratio?")
    _, mock_create = await _run_with_stream_mock(state)

    messages = mock_create.call_args.kwargs["messages"]
    system_content = next(m["content"] for m in messages if m["role"] == "system")
    assert system_content == _SYSTEM_PROMPT_LLM_ONLY


async def test_citations_present_in_rag_mode():
    """Chunks must be rendered as [1]...[n] in the user prompt."""
    state = _make_state(reranked_chunks=[_chunk("Revenue data here")])
    _, mock_create = await _run_with_stream_mock(state)

    messages = mock_create.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "[1]" in user_content
    assert "Revenue data here" in user_content


def _chunk_with_filename(text: str, filename: str) -> dict:
    return {"content": text, "metadata": {"document_filename": filename}, "score": 0.9}


async def test_chunk_rendering_includes_filename():
    """When document_filename is in metadata, it must appear in the rendered chunk prefix."""
    chunk = _chunk_with_filename("AI investment detail", "Apple_2024_10K.pdf")
    state = _make_state(reranked_chunks=[chunk])
    _, mock_create = await _run_with_stream_mock(state)

    messages = mock_create.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "(Apple_2024_10K.pdf)" in user_content
    assert "AI investment detail" in user_content


async def test_chunk_rendering_without_filename_is_plain():
    """When document_filename is absent, prefix must be plain [N] with no '(None)'."""
    state = _make_state(reranked_chunks=[_chunk("Plain content")])
    _, mock_create = await _run_with_stream_mock(state)

    messages = mock_create.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "[1]" in user_content
    assert "Plain content" in user_content
    assert "(None)" not in user_content


async def test_multi_document_chunks_show_distinct_filenames():
    """Two chunks from different documents must both appear with their respective filenames."""
    chunks = [
        _chunk_with_filename("10-K content", "Apple_2024_10K.pdf"),
        _chunk_with_filename("Earnings content", "Apple_Q3_2024_earnings.pdf"),
    ]
    state = _make_state(reranked_chunks=chunks)
    _, mock_create = await _run_with_stream_mock(state)

    messages = mock_create.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "(Apple_2024_10K.pdf)" in user_content
    assert "(Apple_Q3_2024_earnings.pdf)" in user_content
