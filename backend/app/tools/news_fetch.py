from __future__ import annotations

import asyncio

import structlog

from app.config import settings
from app.schemas.tools.news_fetch import NewsArticle, NewsFetchInput, NewsFetchOutput
from app.tools.base import BaseTool, ToolConfigError, ToolUpstreamError

logger = structlog.get_logger(__name__)


def _run_tavily(ticker: str, max_results: int, date_from: str | None, date_to: str | None) -> list[dict]:
    from tavily import TavilyClient

    client = TavilyClient(api_key=settings.TAVILY_API_KEY)
    query = f"{ticker} stock financial news"

    kwargs: dict = {"query": query, "max_results": max_results, "search_depth": "basic"}
    if date_from:
        kwargs["include_answer"] = False

    try:
        response = client.search(**kwargs)
    except Exception as exc:
        raise ToolUpstreamError(f"Tavily search failed: {exc}") from exc

    return response.get("results", [])


class NewsFetchTool(BaseTool[NewsFetchInput, NewsFetchOutput]):
    async def __call__(self, input: NewsFetchInput) -> NewsFetchOutput:  # noqa: A002
        if not settings.TAVILY_API_KEY:
            raise ToolConfigError("TAVILY_API_KEY is not configured")

        logger.debug("tool_called", tool_name="news_fetch", ticker=input.ticker)

        date_from = input.date_from.isoformat() if input.date_from else None
        date_to = input.date_to.isoformat() if input.date_to else None

        if date_from or date_to:
            logger.warning(
                "news_fetch_date_filter_requested",
                ticker=input.ticker,
                date_from=date_from,
                date_to=date_to,
                note="Tavily date filtering support varies by plan; results may not be filtered",
            )

        loop = asyncio.get_event_loop()
        try:
            raw_results = await loop.run_in_executor(
                None, _run_tavily, input.ticker, input.max_results, date_from, date_to
            )
        except ToolUpstreamError:
            raise
        except Exception as exc:
            raise ToolUpstreamError(f"Tavily search failed: {exc}") from exc

        articles = [
            NewsArticle(
                title=r.get("title", ""),
                url=r.get("url", ""),
                summary=r.get("content") or r.get("snippet"),
                published_date=r.get("published_date"),
                source=r.get("source"),
            )
            for r in raw_results[: input.max_results]
        ]

        out = NewsFetchOutput(ticker=input.ticker, articles=articles)
        logger.debug("tool_succeeded", tool_name="news_fetch", article_count=len(articles))
        return out
