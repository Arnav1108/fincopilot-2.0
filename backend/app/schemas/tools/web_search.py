from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WebSearchInput(BaseModel):
    query: str = Field(..., max_length=500)
    search_type: Literal["news", "general", "financial"] = "general"
    max_results: int = Field(5, ge=1, le=10)


class WebSearchResult(BaseModel):
    title: str
    url: str
    content: str
    score: float | None = None
    published_date: str | None = None


class WebSearchOutput(BaseModel):
    results: list[WebSearchResult]
    search_type: str
    query: str
