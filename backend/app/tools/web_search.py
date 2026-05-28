from __future__ import annotations

import asyncio

import structlog

from app.config import settings
from app.schemas.tools.web_search import WebSearchInput, WebSearchOutput, WebSearchResult
from app.tools.base import BaseTool, ToolConfigError, ToolUpstreamError

logger = structlog.get_logger(__name__)


def _run_tavily_web(query: str, search_type: str, max_results: int) -> list[dict]:
    from tavily import TavilyClient

    client = TavilyClient(api_key=settings.TAVILY_API_KEY)
    kwargs: dict = {"query": query, "max_results": max_results, "search_depth": "basic"}

    if search_type == "news":
        kwargs["topic"] = "news"
    elif search_type == "financial":
        # Tavily doesn't document a "finance" topic; attempt it and fall back to general.
        try:
            response = client.search(**{**kwargs, "topic": "finance"})
            return response.get("results", [])
        except Exception:
            pass

    try:
        response = client.search(**kwargs)
    except Exception as exc:
        raise ToolUpstreamError(f"Tavily search failed: {exc}") from exc

    return response.get("results", [])


class WebSearchTool(BaseTool[WebSearchInput, WebSearchOutput]):
    async def __call__(self, input: WebSearchInput) -> WebSearchOutput:  # noqa: A002
        if not settings.TAVILY_API_KEY:
            raise ToolConfigError("TAVILY_API_KEY is not configured")

        logger.debug("tool_called", tool_name="web_search", query=input.query, search_type=input.search_type)

        loop = asyncio.get_running_loop()
        try:
            raw_results = await loop.run_in_executor(
                None, _run_tavily_web, input.query, input.search_type, input.max_results
            )
        except ToolUpstreamError:
            raise
        except Exception as exc:
            raise ToolUpstreamError(f"Tavily search failed: {exc}") from exc

        results = [
            WebSearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content") or r.get("snippet") or "",
                score=r.get("score"),
                published_date=r.get("published_date"),
            )
            for r in raw_results[: input.max_results]
        ]

        out = WebSearchOutput(results=results, search_type=input.search_type, query=input.query)
        logger.debug("tool_succeeded", tool_name="web_search", result_count=len(results))
        return out
