from __future__ import annotations

from pydantic import BaseModel


class PortfolioAnalysisInput(BaseModel):
    user_id: str  # injected by executor from state["user_id"], never supplied by LLM


class HoldingResult(BaseModel):
    ticker: str
    shares: float
    current_price: float | None = None
    current_value: float | None = None
    avg_cost_basis: float | None = None
    gain_loss: float | None = None
    gain_loss_pct: float | None = None
    price_fetch_error: str | None = None


class PortfolioAnalysisOutput(BaseModel):
    total_value: float
    holdings: list[HoldingResult]
    top_gainers: list[str]
    top_losers: list[str]
    message: str | None = None
