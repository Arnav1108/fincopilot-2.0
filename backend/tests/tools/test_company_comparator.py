from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.schemas.tools.company_comparator import CompanyComparatorInput, CompanyComparatorOutput
from app.tools.base import ToolError
from app.tools.company_comparator import CompanyComparatorTool

_TOOL = CompanyComparatorTool()


def _make_ticker_mock(revenue: float) -> MagicMock:
    mock = MagicMock()
    mock.income_stmt = pd.DataFrame(
        {"2023": [revenue]},
        index=["Total Revenue"],
    )
    mock.balance_sheet = pd.DataFrame()
    mock.cashflow = pd.DataFrame()
    fi = MagicMock()
    fi.market_cap = None
    fi.last_price = None
    mock.fast_info = fi
    mock.info = {}
    return mock


@pytest.mark.asyncio
async def test_comparator_returns_output():
    with patch("yfinance.Ticker", return_value=_make_ticker_mock(390e9)):
        result = await _TOOL(CompanyComparatorInput(tickers=["AAPL"], metrics=["revenue"]))

    assert isinstance(result, CompanyComparatorOutput)
    assert len(result.companies) == 1
    assert result.companies[0].ticker == "AAPL"
    assert result.companies[0].metrics["revenue"] == 390e9
    assert result.companies[0].error is None


@pytest.mark.asyncio
async def test_comparator_parallel_fetch():
    tickers_called: list[str] = []

    def _mock_ticker_fn(ticker: str) -> MagicMock:
        tickers_called.append(ticker)
        return _make_ticker_mock(100e9 if ticker == "AAPL" else 200e9)

    with patch("yfinance.Ticker", side_effect=_mock_ticker_fn):
        result = await _TOOL(CompanyComparatorInput(tickers=["AAPL", "MSFT"], metrics=["revenue"]))

    assert set(tickers_called) == {"AAPL", "MSFT"}
    assert len(result.companies) == 2
    tickers_in_output = {c.ticker for c in result.companies}
    assert tickers_in_output == {"AAPL", "MSFT"}


@pytest.mark.asyncio
async def test_comparator_partial_failure():
    def _mock_ticker_fn(ticker: str) -> MagicMock:
        if ticker == "FAIL":
            raise RuntimeError("fetch error")
        return _make_ticker_mock(390e9)

    with patch("yfinance.Ticker", side_effect=_mock_ticker_fn):
        result = await _TOOL(CompanyComparatorInput(tickers=["AAPL", "FAIL"], metrics=["revenue"]))

    assert isinstance(result, CompanyComparatorOutput)
    aapl = next(c for c in result.companies if c.ticker == "AAPL")
    fail = next(c for c in result.companies if c.ticker == "FAIL")
    assert aapl.error is None
    assert aapl.metrics["revenue"] == 390e9
    assert fail.error is not None
    assert fail.metrics["revenue"] is None


@pytest.mark.asyncio
async def test_comparator_all_fail_raises_tool_error():
    with patch("yfinance.Ticker", side_effect=RuntimeError("fetch error")):
        with pytest.raises(ToolError):
            await _TOOL(CompanyComparatorInput(tickers=["AAPL", "MSFT"], metrics=["revenue"]))
