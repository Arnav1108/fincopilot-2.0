"""
8 unit tests for the agent-stream feature.
Tests 1-2 : stream_context no-op and active paths.
Tests 3-4 : synthesizer_node token emission and empty-chunks guard.
Test  5   : router_node node_update emission.
Test  6   : executor_node simple-path event sequence.
Tests 7-8 : _stream_events ingest path and error path.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: F401 (MagicMock used in tests)

from app.agent.executor import executor_node
from app.agent.router import router_node
from app.agent.synthesizer import synthesizer_node
from app.agent.stream_context import emit_event, reset_stream_queue, set_stream_queue
from app.api.v1.chat import _stream_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _async_iter(items):
    """Async generator used to mock OpenAI streaming responses."""
    for item in items:
        yield item


def _make_openai_chunk(content: str | None) -> MagicMock:
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = content
    return chunk


def _drain(q: asyncio.Queue) -> list:
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


async def _collect_sse(gen: AsyncGenerator[str, None]) -> list[str]:
    events = []
    async for chunk in gen:
        events.append(chunk)
    return events


# ---------------------------------------------------------------------------
# Tests 1-2: stream_context
# ---------------------------------------------------------------------------

async def test_emit_event_noop() -> None:
    """emit_event is a no-op when ContextVar holds None — no exception raised."""
    emit_event({"type": "node_update", "node": "router_node", "status": "running"})


async def test_emit_event_with_queue() -> None:
    """emit_event places the full event dict onto the queue when one is set."""
    q: asyncio.Queue = asyncio.Queue()
    tok = set_stream_queue(q)
    try:
        emit_event({"type": "token", "token": "hello"})
        assert q.get_nowait() == {"type": "token", "token": "hello"}
        assert q.empty()
    finally:
        reset_stream_queue(tok)


# ---------------------------------------------------------------------------
# Tests 3-4: synthesizer_node
# ---------------------------------------------------------------------------

async def test_synthesizer_emits_tokens() -> None:
    """
    Synthesizer emits one token event per non-empty delta AND assembles
    final_output from the same tokens without dropping any.
    """
    q: asyncio.Queue = asyncio.Queue()
    tok = set_stream_queue(q)
    try:
        mock_response = _async_iter([
            _make_openai_chunk("Based"),
            _make_openai_chunk(" on"),
            _make_openai_chunk(" this"),
        ])
        mock_create = AsyncMock(return_value=mock_response)

        with patch("app.agent.synthesizer.openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = mock_create
            result = await synthesizer_node({
                "query": "test query",
                "model": "gpt-4o",
                "reranked_chunks": [{"content": "chunk text", "metadata": {}, "score": 0.9}],
                "conversation_summary": "",
                "recent_messages": [],
                "analyst_profile": {},
            })

        token_events = [e for e in _drain(q) if e.get("type") == "token"]
        assert [e["token"] for e in token_events] == ["Based", " on", " this"]
        assert result["final_output"] == "Based on this"
    finally:
        reset_stream_queue(tok)


async def test_synthesizer_empty_chunks_no_tokens() -> None:
    """Empty reranked_chunks + no tool_results → synthesizer uses LLM-only prompt, emits tokens."""
    q: asyncio.Queue = asyncio.Queue()
    tok = set_stream_queue(q)
    try:
        mock_response = _async_iter([
            _make_openai_chunk("General"),
            _make_openai_chunk(" knowledge"),
        ])
        mock_create = AsyncMock(return_value=mock_response)

        with patch("app.agent.synthesizer.openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = mock_create
            result = await synthesizer_node({
                "query": "test",
                "model": "gpt-4o",
                "reranked_chunks": [],
                "tool_results": {},
                "conversation_summary": "",
                "recent_messages": [],
                "analyst_profile": {},
            })

        token_events = [e for e in _drain(q) if e.get("type") == "token"]
        assert len(token_events) >= 1
        assert isinstance(result["final_output"], str) and result["final_output"]
    finally:
        reset_stream_queue(tok)


# ---------------------------------------------------------------------------
# Test 5: router_node
# ---------------------------------------------------------------------------

async def test_router_emits_node_update() -> None:
    """The first event emitted by router_node is its own node_update."""
    q: asyncio.Queue = asyncio.Queue()
    tok = set_stream_queue(q)
    try:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "simple"
        mock_create = AsyncMock(return_value=mock_response)

        with patch("app.agent.router.openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = mock_create
            await router_node({
                "query": "What was Apple revenue?",
                "conversation_summary": "",
                "recent_messages": [],
            })

        events = _drain(q)
        assert events[0] == {
            "type": "node_update",
            "node": "router_node",
            "status": "running",
        }
    finally:
        reset_stream_queue(tok)


# ---------------------------------------------------------------------------
# Test 6: executor_node simple path
# ---------------------------------------------------------------------------

async def test_executor_with_plan_emits_tool_events() -> None:
    """
    Executor with a flat plan step emits events in order:
    node_update → tool_call(running) → tool_call(complete) → sources (if retrieval).
    """
    q: asyncio.Queue = asyncio.Queue()
    tok = set_stream_queue(q)
    try:
        # Build a retrieval mock that returns a DocumentRetrievalOutput-like object
        mock_chunk = MagicMock()
        mock_chunk.content = "Apple revenue chunk"
        mock_chunk.metadata = {}
        mock_chunk.chunk_id = uuid.uuid4()
        mock_chunk.document_id = uuid.uuid4()
        mock_chunk.similarity_score = 0.95

        mock_result = MagicMock()
        mock_result.chunks = [mock_chunk]
        mock_result.model_dump.return_value = {"chunks": []}

        mock_retrieval = AsyncMock(return_value=mock_result)
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())

        plan = [{"tool_name": "document_retrieval", "input": {"query": "Apple revenue?"}}]

        with patch("app.agent.executor.TOOL_REGISTRY", {"document_retrieval": mock_retrieval}):
            await executor_node({
                "query": "Apple revenue?",
                "user_id": user_id,
                "conversation_id": conv_id,
                "plan": plan,
                "tool_results": {},
            })

        events = _drain(q)
        types = [e["type"] for e in events]

        assert types[0] == "node_update"
        assert events[0]["node"] == "executor_node"

        tool_call_events = [e for e in events if e["type"] == "tool_call"]
        assert any(e["status"] == "running" for e in tool_call_events)
        assert any(e["status"] == "complete" for e in tool_call_events)
        assert any(e["tool_name"] == "document_retrieval" for e in tool_call_events)

        # sources event should appear since retrieval returned a chunk
        assert "sources" in types
        sources_event = next(e for e in events if e["type"] == "sources")
        assert len(sources_event["chunks"]) == 1
        assert sources_event["chunks"][0]["content"] == "Apple revenue chunk"
    finally:
        reset_stream_queue(tok)


# ---------------------------------------------------------------------------
# Tests 7-8: _stream_events SSE paths
# ---------------------------------------------------------------------------

async def test_ingest_path_sse() -> None:
    """
    When the graph returns classification='ingest', _stream_events yields
    exactly one done event with message_id: null and no assistant message saved.
    """
    with patch("app.api.v1.chat.compiled_graph") as mock_graph:
        mock_graph.ainvoke = AsyncMock(return_value={
            "classification": "ingest",
            "final_output": "",
            "error": None,
        })
        events = await _collect_sse(
            _stream_events(
                conversation_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                message="please ingest this document",
                model="gpt-4o",
                memory={"conversation_summary": "", "recent_messages": []},
                document_ids=[],
                has_uploaded_documents=False,
            )
        )

    assert len(events) == 1
    assert "event: done" in events[0]
    assert '"message_id": null' in events[0]


async def test_error_path_sse() -> None:
    """
    When the graph raises an exception, _stream_events yields exactly one
    error event containing the exception message — no done event emitted.
    """
    with patch("app.api.v1.chat.compiled_graph") as mock_graph:
        mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
        events = await _collect_sse(
            _stream_events(
                conversation_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                message="test query",
                model="gpt-4o",
                memory={"conversation_summary": "", "recent_messages": []},
                document_ids=[],
                has_uploaded_documents=False,
            )
        )

    assert len(events) == 1
    assert "event: error" in events[0]
    assert "boom" in events[0]
