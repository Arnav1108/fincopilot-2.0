from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import structlog

from app.schemas.tools.financial_data import (
    BalanceSheetData,
    CashFlowData,
    FinancialDataInput,
    FinancialDataOutput,
    IncomeStatementData,
)
from app.tools.base import BaseTool, ToolValidationError

logger = structlog.get_logger(__name__)


def _with_retry(fn, ticker: str, max_attempts: int = 3):
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                logger.warning(
                    "yfinance_retry",
                    ticker=ticker,
                    attempt=attempt,
                    error=str(exc),
                )
                time.sleep(2 ** (attempt - 1))
    raise last_exc  # type: ignore[misc]


def _safe_float(df, *row_keys: str) -> float | None:
    """Return the first non-None value from the first column of df matching any row_key."""
    if df is None or df.empty:
        return None
    for key in row_keys:
        if key in df.index:
            try:
                val = df.loc[key].iloc[0]
                if val is not None and str(val) != "nan":
                    return float(val)
            except (IndexError, TypeError, ValueError):
                continue
    return None


def _fetch_all(ticker_str: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(ticker_str)
    info = {}
    try:
        fast = _with_retry(lambda: t.fast_info, ticker_str)
        info["current_price"] = getattr(fast, "last_price", None)
        info["market_cap"] = getattr(fast, "market_cap", None)
        info["currency"] = getattr(fast, "currency", None)
    except Exception:
        info["current_price"] = None
        info["market_cap"] = None
        info["currency"] = None

    try:
        income = _with_retry(lambda: t.income_stmt, ticker_str)
    except Exception:
        income = None

    try:
        balance = _with_retry(lambda: t.balance_sheet, ticker_str)
    except Exception:
        balance = None

    try:
        cashflow = _with_retry(lambda: t.cashflow, ticker_str)
    except Exception:
        cashflow = None

    info["income"] = income
    info["balance"] = balance
    info["cashflow"] = cashflow
    return info


class FinancialDataTool(BaseTool[FinancialDataInput, FinancialDataOutput]):
    async def __call__(self, input: FinancialDataInput) -> FinancialDataOutput:  # noqa: A002
        import re
        if not re.match(r"^[A-Z0-9]{1,10}$", input.ticker):
            raise ToolValidationError(
                f"ticker must be 1-10 uppercase alphanumeric characters, got: {input.ticker!r}"
            )

        logger.debug("tool_called", tool_name="financial_data", ticker=input.ticker)
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, _fetch_all, input.ticker)

        income_df = data["income"]
        balance_df = data["balance"]
        cf_df = data["cashflow"]

        revenue_cols = income_df.columns.tolist() if income_df is not None and not income_df.empty else []

        def _rev_at(idx: int) -> float | None:
            if income_df is None or income_df.empty:
                return None
            if "Total Revenue" not in income_df.index:
                return None
            row = income_df.loc["Total Revenue"]
            if idx >= len(row):
                return None
            try:
                val = row.iloc[idx]
                return float(val) if str(val) != "nan" else None
            except (TypeError, ValueError):
                return None

        income = IncomeStatementData(
            revenue_current=_rev_at(0),
            revenue_prior_year=_rev_at(1),
            revenue_3yr_ago=_rev_at(3),
            gross_profit=_safe_float(income_df, "Gross Profit"),
            operating_income=_safe_float(income_df, "Operating Income", "EBIT"),
            net_income=_safe_float(income_df, "Net Income"),
            ebitda=_safe_float(income_df, "EBITDA"),
            eps_diluted=_safe_float(income_df, "Diluted EPS"),
        )

        balance = BalanceSheetData(
            total_assets=_safe_float(balance_df, "Total Assets"),
            total_debt=_safe_float(balance_df, "Total Debt", "Long Term Debt"),
            total_equity=_safe_float(balance_df, "Stockholders Equity", "Total Equity Gross Minority Interest"),
            cash_and_equivalents=_safe_float(balance_df, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
        )

        cf = CashFlowData(
            operating_cash_flow=_safe_float(cf_df, "Operating Cash Flow"),
            capex=_safe_float(cf_df, "Capital Expenditure"),
            free_cash_flow=_safe_float(cf_df, "Free Cash Flow"),
            depreciation_amortization=_safe_float(cf_df, "Depreciation And Amortization", "Depreciation Depletion And Amortization"),
        )

        out = FinancialDataOutput(
            ticker=input.ticker,
            currency=data["currency"],
            current_price=float(data["current_price"]) if data["current_price"] is not None else None,
            market_cap=float(data["market_cap"]) if data["market_cap"] is not None else None,
            income_statement=income,
            balance_sheet=balance,
            cash_flow=cf,
            as_of_date=datetime.now(tz=timezone.utc).isoformat(),
        )

        logger.debug("tool_succeeded", tool_name="financial_data", ticker=input.ticker)
        return out
