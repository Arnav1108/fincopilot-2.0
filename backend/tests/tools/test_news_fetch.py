from unittest.mock import patch

import pytest

from app.schemas.tools.news_fetch import NewsFetchInput
from app.tools.base import ToolConfigError, ToolUpstreamError
from app.tools.news_fetch import NewsFetchTool, _run_tavily

_TOOL = NewsFetchTool()

_FAKE_RESULTS = [
    {
        "title": f"Article {i}",
        "url": f"https://example.com/{i}",
        "content": f"Summary {i}",
        "published_date": "2024-01-01",
        "source": "Reuters",
    }
    for i in range(10)
]


@pytest.mark.asyncio
async def test_returns_articles_matching_schema(monkeypatch):
    monkeypatch.setattr("app.config.settings.TAVILY_API_KEY", "test-key")

    with patch("app.tools.news_fetch._run_tavily", return_value=_FAKE_RESULTS):
        result = await _TOOL(NewsFetchInput(ticker="AAPL"))

    assert result.ticker == "AAPL"
    assert len(result.articles) == 10
    assert result.articles[0].title == "Article 0"
    assert result.articles[0].url == "https://example.com/0"


@pytest.mark.asyncio
async def test_max_results_respected(monkeypatch):
    monkeypatch.setattr("app.config.settings.TAVILY_API_KEY", "test-key")

    with patch("app.tools.news_fetch._run_tavily", return_value=_FAKE_RESULTS):
        result = await _TOOL(NewsFetchInput(ticker="AAPL", max_results=3))

    assert len(result.articles) == 3


@pytest.mark.asyncio
async def test_tavily_error_wrapped(monkeypatch):
    monkeypatch.setattr("app.config.settings.TAVILY_API_KEY", "test-key")

    def _raise(*args, **kwargs):
        raise ToolUpstreamError("Tavily search failed: connection error")

    with patch("app.tools.news_fetch._run_tavily", side_effect=_raise):
        with pytest.raises(ToolUpstreamError):
            await _TOOL(NewsFetchInput(ticker="AAPL"))


@pytest.mark.asyncio
async def test_missing_api_key_raises_config_error(monkeypatch):
    monkeypatch.setattr("app.config.settings.TAVILY_API_KEY", "")

    with pytest.raises(ToolConfigError):
        await _TOOL(NewsFetchInput(ticker="AAPL"))
