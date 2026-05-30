from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.tools.web_search import WebSearchInput, WebSearchOutput
from app.tools.base import ToolConfigError, ToolUpstreamError
from app.tools.web_search import WebSearchTool

_TOOL = WebSearchTool()

_SERPER_ORGANIC = [
    {
        "title": f"Article {i}",
        "link": f"https://example.com/{i}",
        "snippet": f"Summary {i}",
        "date": "Jan 1, 2024",
    }
    for i in range(5)
]

_SERPER_NEWS = [
    {
        "title": f"News {i}",
        "link": f"https://news.example.com/{i}",
        "snippet": f"News summary {i}",
        "date": "2 hours ago",
    }
    for i in range(3)
]


def _make_mock_client(status_code: int, json_data: dict):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = json_data

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)

    mock_client_class = MagicMock()
    mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
    mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_client_class, mock_http


@pytest.mark.asyncio
async def test_web_search_returns_output(monkeypatch):
    monkeypatch.setattr("app.config.settings.SERPER_API_KEY", "test-key")

    mock_client_class, mock_http = _make_mock_client(200, {"organic": _SERPER_ORGANIC})

    with patch("httpx.AsyncClient", mock_client_class):
        result = await _TOOL(WebSearchInput(query="AAPL earnings"))

    assert isinstance(result, WebSearchOutput)
    assert result.query == "AAPL earnings"
    assert len(result.results) == 5
    assert result.results[0].title == "Article 0"
    assert result.results[0].url == "https://example.com/0"
    assert result.results[0].content == "Summary 0"
    assert result.results[0].score is None
    assert result.results[0].published_date == "Jan 1, 2024"

    call_args = mock_http.post.call_args
    assert call_args.args[0] == "https://google.serper.dev/search"


@pytest.mark.asyncio
async def test_web_search_news_type(monkeypatch):
    monkeypatch.setattr("app.config.settings.SERPER_API_KEY", "test-key")

    mock_client_class, mock_http = _make_mock_client(200, {"news": _SERPER_NEWS})

    with patch("httpx.AsyncClient", mock_client_class):
        result = await _TOOL(WebSearchInput(query="AAPL news", search_type="news"))

    assert result.search_type == "news"
    assert len(result.results) == 3
    assert result.results[0].title == "News 0"
    assert result.results[0].url == "https://news.example.com/0"
    assert result.results[0].published_date == "2 hours ago"

    call_args = mock_http.post.call_args
    assert call_args.args[0] == "https://google.serper.dev/news"


@pytest.mark.asyncio
async def test_web_search_empty_results(monkeypatch):
    monkeypatch.setattr("app.config.settings.SERPER_API_KEY", "test-key")

    mock_client_class, _ = _make_mock_client(200, {})

    with patch("httpx.AsyncClient", mock_client_class):
        result = await _TOOL(WebSearchInput(query="unknown query"))

    assert isinstance(result, WebSearchOutput)
    assert result.results == []


@pytest.mark.asyncio
async def test_web_search_tool_error_on_failure(monkeypatch):
    monkeypatch.setattr("app.config.settings.SERPER_API_KEY", "test-key")

    mock_http = AsyncMock()
    mock_http.post.side_effect = RuntimeError("connection error")

    mock_client_class = MagicMock()
    mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
    mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", mock_client_class):
        with pytest.raises(ToolUpstreamError):
            await _TOOL(WebSearchInput(query="AAPL earnings"))


@pytest.mark.asyncio
async def test_web_search_missing_api_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.SERPER_API_KEY", "")

    with pytest.raises(ToolConfigError):
        await _TOOL(WebSearchInput(query="AAPL earnings"))
