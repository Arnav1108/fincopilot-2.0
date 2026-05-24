from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.tools.document_finder import DocumentFinderInput, DocumentFinderOutput
from app.tools.base import ToolNotFoundError
from app.tools.document_finder import DocumentFinderTool

_TOOL = DocumentFinderTool()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_input(**overrides) -> DocumentFinderInput:
    base = {
        "ticker": "AAPL",
        "filing_type": "10-K",
        "conversation_id": str(uuid.uuid4()),
    }
    return DocumentFinderInput(**{**base, **overrides})


def _make_db_factory(existing_doc=None, new_doc_id: str | None = None):
    """
    Return a mock AsyncSessionFactory that:
    - For execute() calls: returns existing_doc via scalar_one_or_none (or None)
    - For add/flush/refresh calls: sets doc.id to new_doc_id
    """
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=existing_doc)

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.begin = MagicMock(return_value=mock_db)
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    if new_doc_id:
        mock_db.refresh = AsyncMock(
            side_effect=lambda d: setattr(d, "id", uuid.UUID(new_doc_id))
        )
    else:
        mock_db.refresh = AsyncMock()

    return MagicMock(return_value=mock_db)


def _make_pubsub_that_yields(payload: dict):
    """Async generator that yields one 'message'-type pubsub message, then stops."""
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()

    async def _listen():
        yield {"type": "subscribe", "data": 1}
        yield {"type": "message", "data": json.dumps(payload)}

    mock_pubsub.listen = _listen
    return mock_pubsub


def _make_redis_with_pubsub(pubsub):
    redis = AsyncMock()
    redis.pubsub = MagicMock(return_value=pubsub)
    redis.aclose = AsyncMock()
    return redis


def _make_httpx_response(json_data=None, content=b"filing content"):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.content = content
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    return resp


_SUBMISSIONS_JSON = {
    "filings": {
        "recent": {
            "form": ["10-K", "10-Q"],
            "accessionNumber": ["0000320193-23-000106", "0000320193-23-000077"],
        }
    }
}


# ---------------------------------------------------------------------------
# 1. test_sec_route_returns_ready_output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sec_route_returns_ready_output():
    conv_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    inp = _make_input(ticker="AAPL", filing_type="10-K", conversation_id=conv_id)

    mock_factory = _make_db_factory(existing_doc=None, new_doc_id=doc_id)

    subs_resp = _make_httpx_response(json_data=_SUBMISSIONS_JSON)
    filing_resp = _make_httpx_response(content=b"<html>filing</html>")
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(side_effect=[subs_resp, filing_resp])

    pubsub = _make_pubsub_that_yields({"status": "ready", "chunk_count": 42})
    mock_redis = _make_redis_with_pubsub(pubsub)
    ingest_delay = MagicMock()

    with (
        patch("app.tools.document_finder._get_cik", AsyncMock(return_value="0000320193")),
        patch("httpx.AsyncClient", return_value=mock_http),
        patch("app.tools.document_finder.AsyncSessionFactory", mock_factory),
        patch("app.tools.document_finder.ingest_document") as ingest_patch,
        patch("app.tools.document_finder._redis_client", return_value=mock_redis),
    ):
        ingest_patch.delay = ingest_delay
        result = await _TOOL(inp)

    assert result.status == "ready"
    assert result.chunk_count == 42

    ingest_delay.assert_called_once()
    call_args = ingest_delay.call_args[0]
    assert len(call_args) == 4
    assert conv_id in call_args


# ---------------------------------------------------------------------------
# 2. test_duplicate_returns_early
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_returns_early():
    existing_doc = MagicMock()
    existing_doc.id = uuid.uuid4()
    existing_doc.chunk_count = 10

    mock_factory = _make_db_factory(existing_doc=existing_doc)
    ingest_delay = MagicMock()

    with (
        patch("app.tools.document_finder.AsyncSessionFactory", mock_factory),
        patch("app.tools.document_finder.ingest_document") as ingest_patch,
    ):
        ingest_patch.delay = ingest_delay
        result = await _TOOL(_make_input())

    assert result.status == "duplicate"
    ingest_delay.assert_not_called()


# ---------------------------------------------------------------------------
# 3. test_ticker_not_found_raises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ticker_not_found_raises():
    # EDGAR tickers.json returns empty → _get_cik raises ToolNotFoundError
    mock_factory = _make_db_factory(existing_doc=None)

    tickers_resp = _make_httpx_response(json_data={})
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=tickers_resp)

    import app.tools.document_finder as mod
    mod._CIK_CACHE.clear()

    with (
        patch("app.tools.document_finder.AsyncSessionFactory", mock_factory),
        patch("httpx.AsyncClient", return_value=mock_http),
    ):
        with pytest.raises(ToolNotFoundError):
            await _TOOL(_make_input(ticker="AAPL", filing_type="10-K"))


# ---------------------------------------------------------------------------
# 4. test_web_route_pdf_download
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_route_pdf_download():
    conv_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    pdf_url = "https://example.com/tesla_q3_transcript.pdf"
    inp = _make_input(ticker="TSLA", filing_type="transcript", conversation_id=conv_id)

    mock_factory = _make_db_factory(existing_doc=None, new_doc_id=doc_id)

    pdf_resp = _make_httpx_response(content=b"%PDF-1.4 ...")
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=pdf_resp)

    mock_tavily = MagicMock()
    mock_tavily.search = MagicMock(return_value={"results": [{"url": pdf_url, "title": "Q3 Transcript"}]})

    pubsub = _make_pubsub_that_yields({"status": "ready", "chunk_count": 15})
    mock_redis = _make_redis_with_pubsub(pubsub)
    ingest_delay = MagicMock()

    with (
        patch("app.tools.document_finder.AsyncSessionFactory", mock_factory),
        patch("tavily.TavilyClient", return_value=mock_tavily),
        patch("httpx.AsyncClient", return_value=mock_http),
        patch("app.tools.document_finder.ingest_document") as ingest_patch,
        patch("app.tools.document_finder._redis_client", return_value=mock_redis),
    ):
        ingest_patch.delay = ingest_delay
        result = await _TOOL(inp)

    assert result.status == "ready"

    ingest_delay.assert_called_once()
    call_args = ingest_delay.call_args[0]
    # call: delay(doc_id, tmp_path, file_type, conversation_id)
    file_type_arg = call_args[2]
    assert file_type_arg == "pdf"


# ---------------------------------------------------------------------------
# 5. test_web_route_no_results_raises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_route_no_results_raises():
    inp = _make_input(ticker="TSLA", filing_type="transcript")
    mock_factory = _make_db_factory(existing_doc=None)

    mock_tavily = MagicMock()
    mock_tavily.search = MagicMock(return_value={"results": []})

    with (
        patch("app.tools.document_finder.AsyncSessionFactory", mock_factory),
        patch("tavily.TavilyClient", return_value=mock_tavily),
    ):
        with pytest.raises(ToolNotFoundError):
            await _TOOL(inp)


# ---------------------------------------------------------------------------
# 6. test_ingestion_timeout_returns_failed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingestion_timeout_returns_failed():
    conv_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    inp = _make_input(ticker="AAPL", filing_type="10-K", conversation_id=conv_id)

    mock_factory = _make_db_factory(existing_doc=None, new_doc_id=doc_id)

    subs_resp = _make_httpx_response(json_data=_SUBMISSIONS_JSON)
    filing_resp = _make_httpx_response(content=b"<html>filing</html>")
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(side_effect=[subs_resp, filing_resp])

    # pubsub that never emits a ready/failed message
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()

    async def _never_ready():
        yield {"type": "subscribe", "data": 1}
        # stall indefinitely — wait_for will interrupt this
        await asyncio.sleep(9999)

    mock_pubsub.listen = _never_ready
    mock_redis = _make_redis_with_pubsub(mock_pubsub)
    ingest_delay = MagicMock()

    async def _timeout(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    with (
        patch("app.tools.document_finder._get_cik", AsyncMock(return_value="0000320193")),
        patch("httpx.AsyncClient", return_value=mock_http),
        patch("app.tools.document_finder.AsyncSessionFactory", mock_factory),
        patch("app.tools.document_finder.ingest_document") as ingest_patch,
        patch("app.tools.document_finder._redis_client", return_value=mock_redis),
        patch("app.tools.document_finder.asyncio.wait_for", _timeout),
    ):
        ingest_patch.delay = ingest_delay
        result = await _TOOL(inp)

    assert result.status == "failed"
    assert "timed out" in result.message.lower()
