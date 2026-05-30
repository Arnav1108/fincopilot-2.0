from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import structlog

from app.schemas.tools.company_comparator import (
    CompanyComparatorInput,
    CompanyComparatorOutput,
    CompanyMetrics,
)
from app.tools.base import BaseTool, ToolError, ToolValidationError

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


_INCOME_METRICS: dict[str, str] = {
    "revenue": "Total Revenue",
    "net_income": "Net Income",
    "gross_profit": "Gross Profit",
    "operating_income": "Operating Income",
    "ebitda": "EBITDA",
    "eps": "Diluted EPS",
}

_BALANCE_METRICS: dict[str, str] = {
    "total_assets": "Total Assets",
    "total_debt": "Total Debt",
    "total_equity": "Stockholders Equity",
}

_CASHFLOW_METRICS: dict[str, str] = {
    "free_cash_flow": "Free Cash Flow",
    "operating_cash_flow": "Operating Cash Flow",
}

_ALL_KNOWN_METRICS: frozenset[str] = (
    frozenset(_INCOME_METRICS)
    | frozenset(_BALANCE_METRICS)
    | frozenset(_CASHFLOW_METRICS)
    | frozenset({"market_cap", "current_price"})
)


def _safe_first(df: Any, key: str) -> float | None:
    if df is None:
        return None
    try:
        if key not in df.index:
            return None
        val = df.loc[key].iloc[0]
        if val is not None and str(val) != "nan":
            return float(val)
    except Exception:
        pass
    return None


def _fetch_company(ticker_str: str, metrics: list[str]) -> dict[str, float | None]:
    import yfinance as yf

    t = yf.Ticker(ticker_str)
    income = balance = cashflow = fast_info = None
    info: dict = {}

    if any(m in _INCOME_METRICS for m in metrics):
        try:
            income = _with_retry(lambda: t.income_stmt, ticker_str)
        except Exception:
            pass

    if any(m in _BALANCE_METRICS for m in metrics):
        try:
            balance = _with_retry(lambda: t.balance_sheet, ticker_str)
        except Exception:
            pass

    if any(m in _CASHFLOW_METRICS for m in metrics):
        try:
            cashflow = _with_retry(lambda: t.cashflow, ticker_str)
        except Exception:
            pass

    if any(m in ("market_cap", "current_price") for m in metrics):
        try:
            fast_info = _with_retry(lambda: t.fast_info, ticker_str)
        except Exception:
            pass

    if any(m not in _ALL_KNOWN_METRICS for m in metrics):
        try:
            info = _with_retry(lambda: t.info or {}, ticker_str)
        except Exception:
            pass

    result: dict[str, float | None] = {}
    for metric in metrics:
        if metric in _INCOME_METRICS:
            result[metric] = _safe_first(income, _INCOME_METRICS[metric])
        elif metric in _BALANCE_METRICS:
            result[metric] = _safe_first(balance, _BALANCE_METRICS[metric])
        elif metric in _CASHFLOW_METRICS:
            result[metric] = _safe_first(cashflow, _CASHFLOW_METRICS[metric])
        elif metric == "market_cap":
            result[metric] = float(fast_info.market_cap) if fast_info and fast_info.market_cap is not None else None
        elif metric == "current_price":
            result[metric] = float(fast_info.last_price) if fast_info and fast_info.last_price is not None else None
        else:
            val = info.get(metric)
            try:
                result[metric] = float(val) if val is not None else None
            except (TypeError, ValueError):
                result[metric] = None

    return result


class CompanyComparatorTool(BaseTool[CompanyComparatorInput, CompanyComparatorOutput]):
    async def __call__(self, input: CompanyComparatorInput) -> CompanyComparatorOutput:  # noqa: A002
        for ticker in input.tickers:
            if not re.match(r"^[A-Z0-9]{1,10}$", ticker):
                raise ToolValidationError(f"Invalid ticker: {ticker!r}")

        logger.debug("tool_called", tool_name="company_comparator", tickers=input.tickers, metrics=input.metrics)

        loop = asyncio.get_running_loop()

        async def _fetch_one(ticker: str) -> CompanyMetrics:
            try:
                metrics_dict = await loop.run_in_executor(None, _fetch_company, ticker, input.metrics)
                return CompanyMetrics(ticker=ticker, metrics=metrics_dict)
            except Exception as exc:
                logger.warning("company_comparator_ticker_error", ticker=ticker, error=str(exc))
                return CompanyMetrics(ticker=ticker, metrics={m: None for m in input.metrics}, error=str(exc))

        companies = list(await asyncio.gather(*[_fetch_one(t) for t in input.tickers]))

        if all(c.error is not None for c in companies):
            raise ToolError(
                "All ticker lookups failed: " + "; ".join(c.error for c in companies if c.error)
            )

        out = CompanyComparatorOutput(companies=companies, metrics=input.metrics)
        logger.debug("tool_succeeded", tool_name="company_comparator", ticker_count=len(companies))
        return out
