"""Unit tests for the extract_memories Celery task."""
import json
import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from app.tasks.memory_extraction import _ALLOWED_FACT_TYPES, extract_memories


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_messages(user_text: str = "Tell me about TSLA", assistant_text: str = "Tesla reported strong earnings") -> list[MagicMock]:
    msg_user = MagicMock()
    msg_user.role.value = "user"
    msg_user.content = user_text
    msg_assistant = MagicMock()
    msg_assistant.role.value = "assistant"
    msg_assistant.content = assistant_text
    return [msg_user, msg_assistant]


def _make_db(messages: list[MagicMock], current_count: int = 0) -> MagicMock:
    """Return a mock sync session that replays messages then count queries."""
    mock_db = MagicMock()

    # Query 1: messages
    q_messages = MagicMock()
    q_messages.filter.return_value.order_by.return_value.limit.return_value.all.return_value = list(reversed(messages))

    # Query 2: count
    q_count = MagicMock()
    q_count.filter.return_value.scalar.return_value = current_count

    # Query 3: eviction list (empty unless overridden in specific tests)
    q_evict = MagicMock()
    q_evict.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

    mock_db.query.side_effect = [q_messages, q_count, q_evict]
    return mock_db


def _mock_openai_response(facts: list[dict]) -> MagicMock:
    raw = json.dumps(facts)
    mock_choice = MagicMock()
    mock_choice.message.content = raw
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    return MagicMock(return_value=mock_client)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestExtractionParsing:
    def test_valid_facts_inserted(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        facts = [{"fact_type": "ticker_interest", "content": "User researches TSLA frequently"}]
        mock_db = _make_db(_make_messages())

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", _mock_openai_response(facts)):
            extract_memories.apply(args=[user_id, conv_id])

        mock_db.add_all.assert_called_once()
        inserted = mock_db.add_all.call_args[0][0]
        assert len(inserted) == 1
        assert inserted[0].fact_type == "ticker_interest"
        assert inserted[0].content == "User researches TSLA frequently"
        mock_db.commit.assert_called_once()

    def test_all_allowed_fact_types_accepted(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        facts = [{"fact_type": ft, "content": f"content for {ft}"} for ft in _ALLOWED_FACT_TYPES]
        mock_db = _make_db(_make_messages(), current_count=0)

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", _mock_openai_response(facts)):
            extract_memories.apply(args=[user_id, conv_id])

        inserted = mock_db.add_all.call_args[0][0]
        assert len(inserted) == len(_ALLOWED_FACT_TYPES)


class TestInvalidJSON:
    def test_non_json_response_skips_insertion(self, caplog):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        mock_db = _make_db(_make_messages())

        mock_choice = MagicMock()
        mock_choice.message.content = "Sorry, I cannot extract anything."
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", MagicMock(return_value=mock_client)):
            extract_memories.apply(args=[user_id, conv_id])

        mock_db.add_all.assert_not_called()
        mock_db.commit.assert_not_called()

    def test_non_list_json_skips_insertion(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        mock_db = _make_db(_make_messages())

        mock_choice = MagicMock()
        mock_choice.message.content = '{"fact_type": "ticker_interest", "content": "TSLA"}'
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", MagicMock(return_value=mock_client)):
            extract_memories.apply(args=[user_id, conv_id])

        mock_db.add_all.assert_not_called()


class TestEmptyResponse:
    def test_empty_array_skips_insertion(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        mock_db = _make_db(_make_messages())

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", _mock_openai_response([])):
            extract_memories.apply(args=[user_id, conv_id])

        mock_db.add_all.assert_not_called()
        mock_db.commit.assert_not_called()

    def test_no_messages_returns_early(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        mock_db = MagicMock()
        q_empty = MagicMock()
        q_empty.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        mock_db.query.return_value = q_empty

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI") as mock_oai:
            extract_memories.apply(args=[user_id, conv_id])

        mock_oai.assert_not_called()
        mock_db.add_all.assert_not_called()


class TestFactValidation:
    def test_unknown_fact_type_discarded(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        facts = [
            {"fact_type": "unknown_type", "content": "some content"},
            {"fact_type": "ticker_interest", "content": "TSLA watcher"},
        ]
        mock_db = _make_db(_make_messages())

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", _mock_openai_response(facts)):
            extract_memories.apply(args=[user_id, conv_id])

        inserted = mock_db.add_all.call_args[0][0]
        assert len(inserted) == 1
        assert inserted[0].fact_type == "ticker_interest"

    def test_blank_content_discarded(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        facts = [
            {"fact_type": "ticker_interest", "content": "   "},
            {"fact_type": "sector_interest", "content": "Tech sector follower"},
        ]
        mock_db = _make_db(_make_messages())

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", _mock_openai_response(facts)):
            extract_memories.apply(args=[user_id, conv_id])

        inserted = mock_db.add_all.call_args[0][0]
        assert len(inserted) == 1
        assert inserted[0].fact_type == "sector_interest"

    def test_content_truncated_to_200_chars(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        long_content = "X" * 250
        facts = [{"fact_type": "research_pattern", "content": long_content}]
        mock_db = _make_db(_make_messages())

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", _mock_openai_response(facts)):
            extract_memories.apply(args=[user_id, conv_id])

        inserted = mock_db.add_all.call_args[0][0]
        assert len(inserted) == 1
        assert len(inserted[0].content) == 200


class TestEviction:
    def test_evicts_oldest_when_at_cap(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        # 20 existing facts + 3 new = need to evict 3
        facts = [
            {"fact_type": "ticker_interest", "content": f"fact {i}"} for i in range(3)
        ]
        old_row_1, old_row_2, old_row_3 = MagicMock(), MagicMock(), MagicMock()

        mock_db = MagicMock()
        q_messages = MagicMock()
        q_messages.filter.return_value.order_by.return_value.limit.return_value.all.return_value = list(reversed(_make_messages()))
        q_count = MagicMock()
        q_count.filter.return_value.scalar.return_value = 20
        q_evict = MagicMock()
        q_evict.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            old_row_1, old_row_2, old_row_3
        ]
        mock_db.query.side_effect = [q_messages, q_count, q_evict]

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", _mock_openai_response(facts)):
            extract_memories.apply(args=[user_id, conv_id])

        assert mock_db.delete.call_count == 3
        mock_db.flush.assert_called_once()
        inserted = mock_db.add_all.call_args[0][0]
        assert len(inserted) == 3

    def test_no_eviction_when_under_cap(self):
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        facts = [{"fact_type": "ticker_interest", "content": "AAPL watcher"}]
        mock_db = _make_db(_make_messages(), current_count=5)

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", _mock_openai_response(facts)):
            extract_memories.apply(args=[user_id, conv_id])

        mock_db.delete.assert_not_called()
        mock_db.flush.assert_not_called()
        mock_db.add_all.assert_called_once()

    def test_total_stays_at_cap_after_eviction(self):
        """After eviction + insert the row count must equal USER_MEMORY_MAX_COUNT."""
        user_id = str(uuid.uuid4())
        conv_id = str(uuid.uuid4())
        # 19 existing + 3 new = evict 2
        facts = [{"fact_type": "ticker_interest", "content": f"fact {i}"} for i in range(3)]
        old_1, old_2 = MagicMock(), MagicMock()

        mock_db = MagicMock()
        q_messages = MagicMock()
        q_messages.filter.return_value.order_by.return_value.limit.return_value.all.return_value = list(reversed(_make_messages()))
        q_count = MagicMock()
        q_count.filter.return_value.scalar.return_value = 19
        q_evict = MagicMock()
        q_evict.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [old_1, old_2]
        mock_db.query.side_effect = [q_messages, q_count, q_evict]

        with patch("app.tasks.memory_extraction._SessionLocal", return_value=mock_db), \
             patch("app.tasks.memory_extraction.openai.OpenAI", _mock_openai_response(facts)):
            extract_memories.apply(args=[user_id, conv_id])

        assert mock_db.delete.call_count == 2
        inserted = mock_db.add_all.call_args[0][0]
        assert len(inserted) == 3


class TestPromptPII:
    def test_pii_exclusion_in_system_prompt(self):
        from app.tasks.memory_extraction import _SYSTEM_PROMPT
        prompt_lower = _SYSTEM_PROMPT.lower()
        assert "personally identifiable" in prompt_lower or "pii" in prompt_lower or "email" in prompt_lower
        assert "names" in prompt_lower or "name" in prompt_lower
