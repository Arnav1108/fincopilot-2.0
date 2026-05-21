from __future__ import annotations

from pydantic import BaseModel


class FinancialCalculatorInput(BaseModel):
    current_price: float | None = None
    market_cap: float | None = None
    eps_diluted: float | None = None
    ebitda: float | None = None
    total_debt: float | None = None
    cash_and_equivalents: float | None = None
    total_equity: float | None = None
    net_income: float | None = None
    revenue_current: float | None = None
    revenue_prior_year: float | None = None
    revenue_3yr_ago: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None


class FinancialCalculatorOutput(BaseModel):
    pe_ratio: float | None = None
    ev_ebitda: float | None = None
    roe: float | None = None
    debt_equity_ratio: float | None = None
    revenue_growth_yoy: float | None = None
    revenue_growth_3yr_cagr: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    errors: list[str] = []
