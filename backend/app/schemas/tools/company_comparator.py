from __future__ import annotations

from pydantic import BaseModel, Field


class CompanyComparatorInput(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=10)
    metrics: list[str] = Field(..., min_length=1)


class CompanyMetrics(BaseModel):
    ticker: str
    metrics: dict[str, float | None]
    error: str | None = None


class CompanyComparatorOutput(BaseModel):
    companies: list[CompanyMetrics]
    metrics: list[str]
