from unittest.mock import MagicMock, patch

import pytest

from app.schemas.tools.web_search import WebSearchInput, WebSearchOutput
from app.tools.base import ToolError, ToolUpstreamError
from app.tools.web_search import WebSearchTool

_TOOL = WebSearchTool()

_FAKE_RESULTS = [
    {
        "title": f"Article {i}",
        "url": f"https://example.com/{i}",
        "content": f"Summary {i}",
        "published_date": "2024-01-01",
        "score": 0.9,
    }
    for i in range(5)
]


@pytest.mark.asyncio
async def test_web_search_returns_output(monkeypatch):
    monkeypatch.setattr("app.config.settings.TAVILY_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_client.search.return_value = {"results": _FAKE_RESULTS}

    with patch("tavily.TavilyClient", return_value=mock_client):
        result = await _TOOL(WebSearchInput(query="AAPL earnings"))

    assert isinstance(result, WebSearchOutput)
    assert result.query == "AAPL earnings"
    assert len(result.results) == 5
    assert result.results[0].title == "Article 0"
    assert result.results[0].url == "https://example.com/0"


@pytest.mark.asyncio
async def test_web_search_news_type(monkeypatch):
    monkeypatch.setattr("app.config.settings.TAVILY_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_client.search.return_value = {"results": _FAKE_RESULTS}

    with patch("tavily.TavilyClient", return_value=mock_client):
        result = await _TOOL(WebSearchInput(query="AAPL news", search_type="news"))

    assert result.search_type == "news"
    call_kwargs = mock_client.search.call_args.kwargs
    assert call_kwargs.get("topic") == "news"


@pytest.mark.asyncio
async def test_web_search_tool_error_on_failure(monkeypatch):
    monkeypatch.setattr("app.config.settings.TAVILY_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_client.search.side_effect = RuntimeError("connection error")

    with patch("tavily.TavilyClient", return_value=mock_client):
        with pytest.raises(ToolError):
            await _TOOL(WebSearchInput(query="AAPL earnings"))
