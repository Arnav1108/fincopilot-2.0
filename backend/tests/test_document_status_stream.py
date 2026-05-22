import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import redis
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.user import User

BASE = "/api/v1/documents"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_sse_events(text: str) -> list[dict]:
    """Parse an SSE response body into a list of {"event": ..., "data": ...} dicts."""
    events = []
    current: dict = {}
    for line in text.splitlines():
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


def _make_pubsub_mock(messages: list) -> MagicMock:
    """Return a mock PubSub whose get_message cycles through the list then returns None."""
    responses = list(messages) + [None] * 20
    call_count = {"n": 0}

    async def _get_message(ignore_subscribe_messages=True, timeout=0.0):
        i = call_count["n"]
        call_count["n"] += 1
        return responses[i] if i < len(responses) else None

    mock = MagicMock()
    mock.get_message = _get_message
    return mock


def _noop_subscribe(mock_pubsub):
    """Return an asynccontextmanager that yields the given mock pubsub."""
    @asynccontextmanager
    async def _inner(channel: str):
        yield mock_pubsub
    return _inner


def _mock_redis_ping():
    """Return an object whose .ping() and .aclose() succeed."""
    mock_r = AsyncMock()
    mock_r.ping = AsyncMock(return_value=True)
    mock_r.aclose = AsyncMock()
    return mock_r


def _mock_async_session_factory(doc: Document):
    """Return a mock AsyncSessionFactory that yields a session returning `doc` on execute()."""
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = doc

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    # async with AsyncSessionFactory() as db: — two-level mock
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    return MagicMock(return_value=mock_cm)


# ── Publish unit tests ────────────────────────────────────────────────────────

class TestPublishStatus:
    @staticmethod
    def _mock_db(doc_id: str):
        mock_doc = MagicMock()
        mock_doc.id = uuid.UUID(doc_id)
        mock_doc.user_id = uuid.uuid4()
        mock_doc.filename = "test.pdf"
        mock_db = MagicMock()
        mock_db.get.return_value = mock_doc
        return mock_db, mock_doc

    @staticmethod
    def _mock_reader(page_text: str, n_pages: int = 3):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = page_text
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page] * n_pages
        return mock_reader

    @staticmethod
    def _mock_openai(embedding: list):
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=embedding)] * 20
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        return MagicMock(return_value=mock_client)

    def test_publish_processing(self):
        from app.tasks.ingestion import ingest_document

        doc_id = str(uuid.uuid4())
        page_text = "The quick brown fox jumps over the lazy dog. " * 7
        mock_db, _ = self._mock_db(doc_id)

        with patch("app.tasks.ingestion._SessionLocal", return_value=mock_db), \
             patch("app.tasks.ingestion.pypdf.PdfReader", return_value=self._mock_reader(page_text)), \
             patch("app.tasks.ingestion.openai.OpenAI", self._mock_openai([0.1] * 1536)), \
             patch("app.tasks.ingestion._redis_client") as mock_redis:
            ingest_document.apply(args=[doc_id, "/nonexistent/fake.pdf"])

        calls = mock_redis.publish.call_args_list
        processing_calls = [c for c in calls if json.loads(c.args[1]).get("status") == "processing"]
        assert len(processing_calls) == 1
        payload = json.loads(processing_calls[0].args[1])
        assert payload == {"status": "processing", "chunk_count": None, "error_message": None}
        assert processing_calls[0].args[0] == f"document.{doc_id}.status"

    def test_publish_ready(self):
        from app.tasks.ingestion import ingest_document

        doc_id = str(uuid.uuid4())
        page_text = "The quick brown fox jumps over the lazy dog. " * 7
        mock_db, _ = self._mock_db(doc_id)

        with patch("app.tasks.ingestion._SessionLocal", return_value=mock_db), \
             patch("app.tasks.ingestion.pypdf.PdfReader", return_value=self._mock_reader(page_text)), \
             patch("app.tasks.ingestion.openai.OpenAI", self._mock_openai([0.1] * 1536)), \
             patch("app.tasks.ingestion._redis_client") as mock_redis:
            ingest_document.apply(args=[doc_id, "/nonexistent/fake.pdf"])

        calls = mock_redis.publish.call_args_list
        ready_calls = [c for c in calls if json.loads(c.args[1]).get("status") == "ready"]
        assert len(ready_calls) == 1
        payload = json.loads(ready_calls[0].args[1])
        assert payload["status"] == "ready"
        assert isinstance(payload["chunk_count"], int) and payload["chunk_count"] > 0
        assert payload["error_message"] is None

    def test_publish_failed_no_text(self):
        from app.tasks.ingestion import ingest_document

        doc_id = str(uuid.uuid4())
        mock_db, _ = self._mock_db(doc_id)

        with patch("app.tasks.ingestion._SessionLocal", return_value=mock_db), \
             patch("app.tasks.ingestion.pypdf.PdfReader", return_value=self._mock_reader("")), \
             patch("app.tasks.ingestion._redis_client") as mock_redis:
            ingest_document.apply(args=[doc_id, "/nonexistent/fake.pdf"])

        calls = mock_redis.publish.call_args_list
        failed_calls = [c for c in calls if json.loads(c.args[1]).get("status") == "failed"]
        assert len(failed_calls) == 1
        payload = json.loads(failed_calls[0].args[1])
        assert payload == {"status": "failed", "chunk_count": None, "error_message": "no extractable text"}

    def test_publish_error_does_not_raise(self):
        from app.tasks.ingestion import ingest_document

        doc_id = str(uuid.uuid4())
        page_text = "The quick brown fox jumps over the lazy dog. " * 7
        mock_db, mock_doc = self._mock_db(doc_id)

        with patch("app.tasks.ingestion._SessionLocal", return_value=mock_db), \
             patch("app.tasks.ingestion.pypdf.PdfReader", return_value=self._mock_reader(page_text)), \
             patch("app.tasks.ingestion.openai.OpenAI", self._mock_openai([0.1] * 1536)), \
             patch("app.tasks.ingestion._redis_client") as mock_redis:
            mock_redis.publish.side_effect = redis.ConnectionError("refused")
            ingest_document.apply(args=[doc_id, "/nonexistent/fake.pdf"])

        assert mock_doc.status == DocumentStatus.ready


# ── SSE endpoint integration tests ────────────────────────────────────────────

class TestStreamDocumentStatus:
    async def _make_doc(
        self,
        db: AsyncSession,
        user: User,
        status: DocumentStatus = DocumentStatus.pending,
        chunk_count: int | None = None,
        error_message: str | None = None,
    ) -> Document:
        doc = Document(
            user_id=user.id,
            filename="test.pdf",
            doc_type=DocumentType.other,
            status=status,
            chunk_count=chunk_count,
            error_message=error_message,
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
        return doc

    async def test_stream_already_ready(
        self, client: AsyncClient, db: AsyncSession, test_user: User
    ):
        doc = await self._make_doc(db, test_user, DocumentStatus.ready, chunk_count=5)
        mock_pubsub = _make_pubsub_mock([])

        with patch("app.api.v1.documents.subscribe_to_channel", _noop_subscribe(mock_pubsub)), \
             patch("app.api.v1.documents.get_async_redis", return_value=_mock_redis_ping()), \
             patch("app.api.v1.documents.AsyncSessionFactory", _mock_async_session_factory(doc)):
            r = await client.get(f"{BASE}/{doc.id}/status-stream")

        assert r.status_code == 200
        events = _parse_sse_events(r.text)
        assert len(events) == 1
        assert events[0]["event"] == "status"
        assert events[0]["data"]["status"] == "ready"
        assert events[0]["data"]["chunk_count"] == 5

    async def test_stream_already_failed(
        self, client: AsyncClient, db: AsyncSession, test_user: User
    ):
        doc = await self._make_doc(db, test_user, DocumentStatus.failed, error_message="bad pdf")
        mock_pubsub = _make_pubsub_mock([])

        with patch("app.api.v1.documents.subscribe_to_channel", _noop_subscribe(mock_pubsub)), \
             patch("app.api.v1.documents.get_async_redis", return_value=_mock_redis_ping()), \
             patch("app.api.v1.documents.AsyncSessionFactory", _mock_async_session_factory(doc)):
            r = await client.get(f"{BASE}/{doc.id}/status-stream")

        assert r.status_code == 200
        events = _parse_sse_events(r.text)
        assert len(events) == 1
        assert events[0]["event"] == "status"
        assert events[0]["data"]["status"] == "failed"
        assert events[0]["data"]["error_message"] == "bad pdf"

    async def test_stream_404_unknown(self, client: AsyncClient):
        r = await client.get(f"{BASE}/{uuid.uuid4()}/status-stream")
        assert r.status_code == 404

    async def test_stream_404_other_user(
        self,
        client: AsyncClient,
        db: AsyncSession,
        test_user_2: User,
    ):
        doc = await self._make_doc(db, test_user_2)
        r = await client.get(f"{BASE}/{doc.id}/status-stream")
        assert r.status_code == 404

    async def test_stream_unauthenticated(self, anon_client: AsyncClient):
        r = await anon_client.get(f"{BASE}/{uuid.uuid4()}/status-stream")
        assert r.status_code == 401

    async def test_stream_processing_to_ready(
        self, client: AsyncClient, db: AsyncSession, test_user: User
    ):
        doc = await self._make_doc(db, test_user, DocumentStatus.processing)
        redis_msg = {
            "type": "message",
            "channel": f"document.{doc.id}.status",
            "data": json.dumps({"status": "ready", "chunk_count": 3, "error_message": None}),
        }
        mock_pubsub = _make_pubsub_mock([redis_msg])

        with patch("app.api.v1.documents.subscribe_to_channel", _noop_subscribe(mock_pubsub)), \
             patch("app.api.v1.documents.get_async_redis", return_value=_mock_redis_ping()), \
             patch("app.api.v1.documents.AsyncSessionFactory", _mock_async_session_factory(doc)):
            r = await client.get(f"{BASE}/{doc.id}/status-stream")

        assert r.status_code == 200
        events = _parse_sse_events(r.text)
        assert len(events) == 2
        assert events[0]["event"] == "status"
        assert events[0]["data"]["status"] == "processing"
        assert events[1]["event"] == "status"
        assert events[1]["data"]["status"] == "ready"
        assert events[1]["data"]["chunk_count"] == 3

    async def test_stream_ping(
        self, client: AsyncClient, db: AsyncSession, test_user: User
    ):
        doc = await self._make_doc(db, test_user, DocumentStatus.processing)
        # time_values drives the loop:
        #   start = t0, last_event = t0
        #   iteration 1: timeout check (t0 < t0+300), get_message→None, keepalive check (t0 == t0 → no ping)
        #   iteration 2: timeout check (t0 < t0+300), get_message→None, keepalive check (t0+61 - t0 = 61 ≥ 60 → ping)
        #   iteration 3: timeout check (t0+301 ≥ 300 → emit timeout, return)
        t0 = 1000.0
        time_values = [
            t0,        # start
            t0,        # last_event initialisation
            t0,        # loop iter 1 — timeout check
            t0,        # loop iter 1 — keepalive check (since_last = 0)
            t0,        # loop iter 2 — timeout check
            t0 + 61,   # loop iter 2 — keepalive check (since_last = 61 → emit ping)
            t0 + 61,   # reset last_event
            t0 + 301,  # loop iter 3 — timeout check → emit timeout + return
        ]
        mock_pubsub = _make_pubsub_mock([None] * 10)

        with patch("app.api.v1.documents.subscribe_to_channel", _noop_subscribe(mock_pubsub)), \
             patch("app.api.v1.documents.get_async_redis", return_value=_mock_redis_ping()), \
             patch("app.api.v1.documents.AsyncSessionFactory", _mock_async_session_factory(doc)), \
             patch("app.api.v1.documents.time") as mock_time:
            mock_time.monotonic.side_effect = time_values
            r = await client.get(f"{BASE}/{doc.id}/status-stream")

        assert r.status_code == 200
        events = _parse_sse_events(r.text)
        event_types = [e["event"] for e in events]
        assert "ping" in event_types

    async def test_stream_timeout(
        self, client: AsyncClient, db: AsyncSession, test_user: User
    ):
        doc = await self._make_doc(db, test_user, DocumentStatus.processing)
        t0 = 1000.0
        time_values = [
            t0,        # start
            t0,        # last_event initialisation
            t0 + 301,  # loop iter 1 — timeout check → emit timeout + return
        ]
        mock_pubsub = _make_pubsub_mock([None] * 10)

        with patch("app.api.v1.documents.subscribe_to_channel", _noop_subscribe(mock_pubsub)), \
             patch("app.api.v1.documents.get_async_redis", return_value=_mock_redis_ping()), \
             patch("app.api.v1.documents.AsyncSessionFactory", _mock_async_session_factory(doc)), \
             patch("app.api.v1.documents.time") as mock_time:
            mock_time.monotonic.side_effect = time_values
            r = await client.get(f"{BASE}/{doc.id}/status-stream")

        assert r.status_code == 200
        events = _parse_sse_events(r.text)
        assert events[-1]["event"] == "timeout"
        assert "maximum duration" in events[-1]["data"]["message"]
