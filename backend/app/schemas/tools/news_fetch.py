from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class NewsFetchInput(BaseModel):
    ticker: str
    date_from: date | None = None
    date_to: date | None = None
    max_results: int = Field(10, ge=1, le=25)


class NewsArticle(BaseModel):
    title: str
    url: str
    summary: str | None = None
    published_date: str | None = None
    source: str | None = None


class NewsFetchOutput(BaseModel):
    ticker: str
    articles: list[NewsArticle]
