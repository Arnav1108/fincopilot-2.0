from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.schemas.tools.financial_data import FinancialDataInput
from app.tools.base import ToolValidationError
from app.tools.financial_data import FinancialDataTool

_TOOL = FinancialDataTool()


def _make_income_df():
    return pd.DataFrame(
        {
            "2023": [390e9, 170e9, 110e9, 96e9, 130e9, 6.13],
            "2022": [360e9, 155e9, 100e9, 90e9, 120e9, 5.8],
            "2021": [330e9, 140e9, 90e9, 80e9, 110e9, 5.0],
            "2020": [300e9, 125e9, 75e9, 70e9, 100e9, 4.5],
        },
        index=["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "EBITDA", "Diluted EPS"],
    )


def _make_balance_df():
    return pd.DataFrame(
        {"2023": [300e9, 100e9, 80e9, 50e9]},
        index=["Total Assets", "Total Debt", "Stockholders Equity", "Cash And Cash Equivalents"],
    )


def _make_cf_df():
    return pd.DataFrame(
        {"2023": [120e9, -12e9, 108e9, 10e9]},
        index=["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow", "Depreciation And Amortization"],
    )


def _make_fast_info():
    fi = MagicMock()
    fi.last_price = 150.0
    fi.market_cap = 2_400e9
    fi.currency = "USD"
    return fi


@pytest.mark.asyncio
async def test_full_output_schema():
    mock_ticker = MagicMock()
    mock_ticker.fast_info = _make_fast_info()
    mock_ticker.income_stmt = _make_income_df()
    mock_ticker.balance_sheet = _make_balance_df()
    mock_ticker.cashflow = _make_cf_df()

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = await _TOOL(FinancialDataInput(ticker="AAPL"))

    assert result.ticker == "AAPL"
    assert result.current_price == 150.0
    assert result.income_statement.revenue_current == 390e9
    assert result.income_statement.revenue_prior_year == 360e9
    assert result.income_statement.revenue_3yr_ago == 300e9
    assert result.balance_sheet.total_debt == 100e9
    assert result.cash_flow.free_cash_flow == 108e9
    assert result.currency == "USD"


@pytest.mark.asyncio
async def test_partial_missing_fields():
    mock_ticker = MagicMock()
    mock_ticker.fast_info = _make_fast_info()
    mock_ticker.income_stmt = _make_income_df()
    mock_ticker.balance_sheet = _make_balance_df()
    mock_ticker.cashflow = pd.DataFrame()  # empty

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = await _TOOL(FinancialDataInput(ticker="AAPL"))

    assert result.income_statement.revenue_current == 390e9
    assert result.cash_flow.operating_cash_flow is None
    assert result.cash_flow.free_cash_flow is None


@pytest.mark.asyncio
async def test_invalid_ticker_raises_validation_error():
    with pytest.raises(ToolValidationError):
        await _TOOL(FinancialDataInput.model_construct(ticker="a b"))


@pytest.mark.asyncio
async def test_runs_in_executor():
    mock_ticker = MagicMock()
    mock_ticker.fast_info = _make_fast_info()
    mock_ticker.income_stmt = _make_income_df()
    mock_ticker.balance_sheet = _make_balance_df()
    mock_ticker.cashflow = _make_cf_df()

    executor_called = []

    import asyncio
    original_run_in_executor = asyncio.get_event_loop().__class__.run_in_executor

    async def patched_run_in_executor(self, executor, func, *args):
        executor_called.append(True)
        return await original_run_in_executor(self, executor, func, *args)

    with patch("yfinance.Ticker", return_value=mock_ticker):
        with patch.object(asyncio.get_event_loop().__class__, "run_in_executor", patched_run_in_executor):
            await _TOOL(FinancialDataInput(ticker="AAPL"))

    assert executor_called, "run_in_executor was not called"
