from __future__ import annotations

from pydantic import BaseModel, Field


class FinancialDataInput(BaseModel):
    ticker: str = Field(..., pattern=r"^[A-Z0-9]{1,10}$")


class IncomeStatementData(BaseModel):
    revenue_current: float | None = None
    revenue_prior_year: float | None = None
    revenue_3yr_ago: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    ebitda: float | None = None
    eps_diluted: float | None = None


class BalanceSheetData(BaseModel):
    total_assets: float | None = None
    total_debt: float | None = None
    total_equity: float | None = None
    cash_and_equivalents: float | None = None


class CashFlowData(BaseModel):
    operating_cash_flow: float | None = None
    capex: float | None = None
    free_cash_flow: float | None = None
    depreciation_amortization: float | None = None


class FinancialDataOutput(BaseModel):
    ticker: str
    currency: str | None = None
    current_price: float | None = None
    market_cap: float | None = None
    income_statement: IncomeStatementData
    balance_sheet: BalanceSheetData
    cash_flow: CashFlowData
    as_of_date: str
