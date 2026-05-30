"""Unit tests for app.agent.state — no real DB or OpenAI calls."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

from app.agent.state import (
    AgentState,
    ChunkDict,
    MemoryManager,
    PlanStep,
    RecentMessage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONV_ID = str(uuid.uuid4())
_USER_ID = str(uuid.uuid4())


def _make_conv(rolling_summary: str | None = "Prior summary") -> MagicMock:
    conv = MagicMock()
    conv.id = uuid.UUID(_CONV_ID)
    conv.rolling_summary = rolling_summary
    return conv


def _make_msg(role_value: str, content: str, ts: datetime | None = None) -> MagicMock:
    msg = MagicMock()
    msg.role = MagicMock()
    msg.role.value = role_value
    msg.content = content
    msg.created_at = ts or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return msg


def _make_db(conv: MagicMock | None, messages: list[MagicMock]) -> AsyncMock:
    db = AsyncMock()
    result1 = MagicMock()
    result1.scalar_one_or_none.return_value = conv
    result2 = MagicMock()
    result2.scalars.return_value.all.return_value = messages
    db.execute.side_effect = [result1, result2]
    return db


def _make_openai_client(content: str = "New summary") -> AsyncMock:
    client = AsyncMock(spec=openai.AsyncOpenAI)
    resp = MagicMock()
    resp.choices[0].message.content = content
    # Explicitly set as AsyncMock — spec= doesn't auto-spec nested async attrs
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# TypedDict shape tests
# ---------------------------------------------------------------------------

def test_plan_step_fields():
    step: PlanStep = {"tool_name": "document_retrieval", "input": {"query": "revenue"}}
    assert step["tool_name"] == "document_retrieval"
    assert step["input"] == {"query": "revenue"}


def test_recent_message_fields():
    msg: RecentMessage = {"role": "user", "content": "Hello"}
    assert msg["role"] == "user"


def test_chunk_dict_fields():
    chunk: ChunkDict = {"content": "text", "metadata": {"page": 1}, "score": 0.95}
    assert chunk["score"] == 0.95


def test_agent_state_fields():
    s: AgentState = {
        "user_id": "u",
        "conversation_id": "c",
        "analyst_profile": {},
        "query": "What is the PE ratio?",
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
    # Must be JSON-serializable — no ORM objects, UUIDs, or datetimes
    serialized = json.dumps(s)
    assert "gpt-4o" in serialized


# ---------------------------------------------------------------------------
# MemoryManager.load_memory tests
# ---------------------------------------------------------------------------

async def test_load_memory_happy_path():
    m1 = _make_msg("user", "oldest", datetime(2024, 1, 1, tzinfo=timezone.utc))
    m2 = _make_msg("assistant", "middle", datetime(2024, 1, 2, tzinfo=timezone.utc))
    m3 = _make_msg("user", "newest", datetime(2024, 1, 3, tzinfo=timezone.utc))
    # DB returns DESC order (newest first); load_memory reverses to chronological
    db = _make_db(_make_conv("Prior summary"), [m3, m2, m1])

    result = await MemoryManager().load_memory(db, _USER_ID, _CONV_ID)

    assert result["conversation_summary"] == "Prior summary"
    assert len(result["recent_messages"]) == 3
    # After reversal, first item should be oldest
    assert result["recent_messages"][0]["content"] == "oldest"
    assert result["recent_messages"][2]["content"] == "newest"
    # Roles must be plain strings, not enum members
    assert isinstance(result["recent_messages"][0]["role"], str)


async def test_load_memory_no_summary():
    db = _make_db(_make_conv(rolling_summary=None), [])
    result = await MemoryManager().load_memory(db, _USER_ID, _CONV_ID)
    assert result["conversation_summary"] == ""


async def test_load_memory_no_messages():
    db = _make_db(_make_conv(), [])
    result = await MemoryManager().load_memory(db, _USER_ID, _CONV_ID)
    assert result["recent_messages"] == []


async def test_load_memory_fewer_than_3_messages():
    msgs = [
        _make_msg("user", "first", datetime(2024, 1, 2, tzinfo=timezone.utc)),
        _make_msg("assistant", "second", datetime(2024, 1, 1, tzinfo=timezone.utc)),
    ]
    # DESC from DB: newest first
    db = _make_db(_make_conv(), msgs)
    result = await MemoryManager().load_memory(db, _USER_ID, _CONV_ID)
    assert len(result["recent_messages"]) == 2


async def test_load_memory_conversation_not_found():
    db = AsyncMock()
    result1 = MagicMock()
    result1.scalar_one_or_none.return_value = None
    db.execute.side_effect = [result1]

    with pytest.raises(ValueError, match="conversation not found"):
        await MemoryManager().load_memory(db, _USER_ID, _CONV_ID)


async def test_load_memory_user_id_scoped():
    db = _make_db(_make_conv(), [])
    await MemoryManager().load_memory(db, _USER_ID, _CONV_ID)

    # Inspect the first execute call's SELECT statement
    first_stmt = db.execute.call_args_list[0][0][0]
    compiled = str(first_stmt.compile())
    assert "user_id" in compiled


# ---------------------------------------------------------------------------
# MemoryManager.regenerate_summary tests
# ---------------------------------------------------------------------------

async def test_regenerate_summary_happy_path():
    client = _make_openai_client("New summary")
    msgs: list[RecentMessage] = [{"role": "user", "content": "Tell me about AAPL"}]
    result = await MemoryManager().regenerate_summary("Old summary", msgs, client)
    assert result == "New summary"


async def test_regenerate_summary_includes_prior_summary():
    client = _make_openai_client("New summary")
    msgs: list[RecentMessage] = [{"role": "user", "content": "question"}]
    await MemoryManager().regenerate_summary("PRIOR TEXT", msgs, client)

    call_kwargs = client.chat.completions.create.call_args
    messages_arg = call_kwargs[1]["messages"]
    user_msg_content = next(m["content"] for m in messages_arg if m["role"] == "user")
    assert "PRIOR TEXT" in user_msg_content


async def test_regenerate_summary_no_prior_summary():
    client = _make_openai_client("Result")
    msgs: list[RecentMessage] = [{"role": "user", "content": "question"}]
    await MemoryManager().regenerate_summary("", msgs, client)

    call_kwargs = client.chat.completions.create.call_args
    messages_arg = call_kwargs[1]["messages"]
    user_msg_content = next(m["content"] for m in messages_arg if m["role"] == "user")
    assert "Prior summary" not in user_msg_content


async def test_regenerate_summary_empty_messages():
    client = _make_openai_client("Should not be called")
    result = await MemoryManager().regenerate_summary("existing summary", [], client)

    client.chat.completions.create.assert_not_called()
    assert result == "existing summary"


async def test_regenerate_summary_uses_mini_model():
    client = _make_openai_client("ok")
    msgs: list[RecentMessage] = [{"role": "user", "content": "q"}]
    await MemoryManager().regenerate_summary("", msgs, client)

    call_kwargs = client.chat.completions.create.call_args
    assert call_kwargs[1]["model"] == "gpt-4o-mini"
