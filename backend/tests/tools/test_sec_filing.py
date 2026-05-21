import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.tools.sec_filing import SECFilingInput, SECFilingIngestOutput, SECFilingPreviewOutput
from app.tools.base import ToolConfigError, ToolNotFoundError, ToolRateLimitError, ToolValidationError
from app.tools.sec_filing import SECFilingFetchTool

_TOOL = SECFilingFetchTool()

_TICKERS_JSON = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
}

_SUBMISSIONS_JSON = {
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "form": ["10-K", "10-Q"],
            "accessionNumber": ["0000320193-23-000106", "0000320193-23-000077"],
            "filingDate": ["2023-11-03", "2023-08-04"],
        }
    },
}

_INDEX_JSON = {
    "directory": {
        "item": [
            {"name": "aapl-20230930.htm", "type": "10-K"},
            {"name": "R1.htm", "type": ""},
        ]
    }
}

_PREVIEW_TEXT = "x" * 3000


def _make_httpx_response(status_code: int, data, is_text: bool = False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if is_text:
        resp.text = data
    else:
        resp.json = MagicMock(return_value=data)
    return resp


def _mock_redis(cached_value=None, incr_return=1):
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(cached_value) if cached_value else None)
    redis.setex = AsyncMock()
    redis.aclose = AsyncMock()

    lua_mock = AsyncMock(return_value=incr_return)
    redis.script_load = AsyncMock(return_value="abc123")
    redis.evalsha = lua_mock
    return redis


@pytest.mark.asyncio
async def test_preview_returns_correct_schema(monkeypatch):
    monkeypatch.setattr("app.config.settings.SEC_EDGAR_CONTACT_EMAIL", "test@example.com")

    redis = _mock_redis()
    tickers_resp = _make_httpx_response(200, _TICKERS_JSON)
    submissions_resp = _make_httpx_response(200, _SUBMISSIONS_JSON)
    index_resp = _make_httpx_response(200, _INDEX_JSON)
    doc_resp = _make_httpx_response(200, None, is_text=True)
    doc_resp.text = _PREVIEW_TEXT

    http_responses = [tickers_resp, submissions_resp, index_resp, doc_resp]

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=http_responses)

    import app.tools.sec_filing as mod
    mod._CIK_CACHE.clear()

    with patch("app.tools.sec_filing._redis_client", return_value=redis):
        with patch("app.tools.sec_filing.edgar_rate_limiter.acquire", AsyncMock()):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await _TOOL(SECFilingInput(ticker="AAPL", filing_type="10-K"))

    assert isinstance(result, SECFilingPreviewOutput)
    assert result.company_name == "Apple Inc."
    assert result.form_type == "10-K"
    assert result.preview == _PREVIEW_TEXT[:2000]
    assert result.confirmation_token


@pytest.mark.asyncio
async def test_cache_hit_skips_edgar_request(monkeypatch):
    monkeypatch.setattr("app.config.settings.SEC_EDGAR_CONTACT_EMAIL", "test@example.com")

    cached_preview = SECFilingPreviewOutput(
        preview="cached preview",
        confirmation_token="abc",
        company_name="Apple Inc.",
        filed_date="2023-11-03",
        filing_url="https://example.com/filing.htm",
        form_type="10-K",
    )
    redis = _mock_redis(cached_value=cached_preview.model_dump())

    mock_http_get = AsyncMock()

    with patch("app.tools.sec_filing._redis_client", return_value=redis):
        with patch("httpx.AsyncClient.get", mock_http_get):
            result = await _TOOL(SECFilingInput(ticker="AAPL", filing_type="10-K"))

    assert isinstance(result, SECFilingPreviewOutput)
    assert result.preview == "cached preview"
    mock_http_get.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_raises_after_timeout(monkeypatch):
    monkeypatch.setattr("app.config.settings.SEC_EDGAR_CONTACT_EMAIL", "test@example.com")

    redis = _mock_redis(incr_return=11)

    import app.tools.rate_limiter as rl_mod
    limiter = rl_mod.RedisTokenBucket()
    limiter._client = redis
    limiter._script_sha = "abc123"

    with pytest.raises(ToolRateLimitError):
        import app.tools.sec_filing as mod
        mod._CIK_CACHE.clear()
        with patch("app.tools.sec_filing._redis_client", return_value=_mock_redis()):
            with patch("app.tools.sec_filing.edgar_rate_limiter", limiter):
                await _TOOL(SECFilingInput(ticker="AAPL", filing_type="10-K"))


@pytest.mark.asyncio
async def test_ingest_dispatched_with_valid_token(monkeypatch, tmp_path):
    monkeypatch.setattr("app.config.settings.SEC_EDGAR_CONTACT_EMAIL", "test@example.com")

    token = uuid.uuid4().hex
    confirm_data = {
        "ticker": "AAPL",
        "filing_type": "10-K",
        "filing_url": "https://example.com/filing.htm",
        "company_name": "Apple Inc.",
        "filed_date": "2023-11-03",
    }
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(confirm_data))
    redis.aclose = AsyncMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    stream_ctx = AsyncMock()
    stream_ctx.__aenter__ = AsyncMock(return_value=stream_ctx)
    stream_ctx.__aexit__ = AsyncMock(return_value=False)
    stream_ctx.raise_for_status = MagicMock()

    async def _aiter_bytes(chunk_size=None):
        yield b"filing content"

    stream_ctx.aiter_bytes = _aiter_bytes
    mock_client.stream = MagicMock(return_value=stream_ctx)

    mock_doc_id = str(uuid.uuid4())
    mock_doc = MagicMock()
    mock_doc.id = uuid.UUID(mock_doc_id)

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.begin = MagicMock(return_value=mock_db)
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock(side_effect=lambda d: setattr(d, "id", uuid.UUID(mock_doc_id)))
    mock_factory = MagicMock(return_value=mock_db)

    ingest_mock = MagicMock()

    system_user_id = uuid.uuid4()

    with patch("app.tools.sec_filing._redis_client", return_value=redis):
        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("app.tools.sec_filing.AsyncSessionFactory", mock_factory):
                with patch("app.tools.sec_filing.ingest_document") as ingest_patch:
                    with patch("app.tools.sec_filing._get_system_user_id", AsyncMock(return_value=system_user_id)):
                        ingest_patch.delay = ingest_mock
                        result = await _TOOL(SECFilingInput(ticker="AAPL", filing_type="10-K", confirmation_token=token))

    assert isinstance(result, SECFilingIngestOutput)
    assert result.status == "pending"
    ingest_mock.assert_called_once()


@pytest.mark.asyncio
async def test_expired_token_raises_validation_error(monkeypatch):
    monkeypatch.setattr("app.config.settings.SEC_EDGAR_CONTACT_EMAIL", "test@example.com")

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.aclose = AsyncMock()

    with patch("app.tools.sec_filing._redis_client", return_value=redis):
        with pytest.raises(ToolValidationError, match="expired or invalid"):
            await _TOOL(SECFilingInput(ticker="AAPL", filing_type="10-K", confirmation_token="deadbeef"))


@pytest.mark.asyncio
async def test_unknown_ticker_raises_not_found(monkeypatch):
    monkeypatch.setattr("app.config.settings.SEC_EDGAR_CONTACT_EMAIL", "test@example.com")

    redis = _mock_redis()
    tickers_without_aapl = {"0": {"cik_str": 789019, "ticker": "GOOGL", "title": "Alphabet Inc."}}
    tickers_resp = _make_httpx_response(200, tickers_without_aapl)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=tickers_resp)

    import app.tools.sec_filing as mod
    mod._CIK_CACHE.clear()

    with patch("app.tools.sec_filing._redis_client", return_value=redis):
        with patch("app.tools.sec_filing.edgar_rate_limiter.acquire", AsyncMock()):
            with patch("httpx.AsyncClient", return_value=mock_client):
                with pytest.raises(ToolNotFoundError, match="AAPL"):
                    await _TOOL(SECFilingInput(ticker="AAPL", filing_type="10-K"))


@pytest.mark.asyncio
async def test_missing_contact_email_raises_config_error(monkeypatch):
    monkeypatch.setattr("app.config.settings.SEC_EDGAR_CONTACT_EMAIL", "")

    with pytest.raises(ToolConfigError, match="SEC_EDGAR_CONTACT_EMAIL"):
        await _TOOL(SECFilingInput(ticker="AAPL", filing_type="10-K"))
