from __future__ import annotations

import httpx
import structlog

from app.config import settings
from app.schemas.tools.web_search import WebSearchInput, WebSearchOutput, WebSearchResult
from app.tools.base import BaseTool, ToolConfigError, ToolUpstreamError

logger = structlog.get_logger(__name__)

_SERPER_SEARCH_URL = "https://google.serper.dev/search"
_SERPER_NEWS_URL = "https://google.serper.dev/news"


class WebSearchTool(BaseTool[WebSearchInput, WebSearchOutput]):
    async def __call__(self, input: WebSearchInput) -> WebSearchOutput:  # noqa: A002
        if not settings.SERPER_API_KEY:
            raise ToolConfigError("SERPER_API_KEY is not configured")

        logger.debug("tool_called", tool_name="web_search", query=input.query, search_type=input.search_type)

        if input.search_type == "news":
            url = _SERPER_NEWS_URL
            query = input.query
        elif input.search_type == "financial":
            url = _SERPER_SEARCH_URL
            query = f"{input.query} financial"
        else:
            url = _SERPER_SEARCH_URL
            query = input.query

        payload = {"q": query, "num": min(input.max_results, 10)}
        headers = {
            "X-API-KEY": settings.SERPER_API_KEY,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=headers)
        except Exception as exc:
            raise ToolUpstreamError(f"Serper search request failed: {exc}") from exc

        if response.status_code != 200:
            raise ToolUpstreamError(f"Serper search returned status {response.status_code}")

        data = response.json()

        if input.search_type == "news":
            raw_results = data.get("news", [])
        else:
            raw_results = data.get("organic", [])

        results = [
            WebSearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                content=item.get("snippet", ""),
                score=None,
                published_date=item.get("date"),
            )
            for item in raw_results[: input.max_results]
        ]

        out = WebSearchOutput(results=results, search_type=input.search_type, query=input.query)
        logger.debug("tool_succeeded", tool_name="web_search", result_count=len(results))
        return out
