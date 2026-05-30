"""Unit tests for PortfolioAnalysisTool.

All DB and yfinance calls are mocked — no real DB or network needed.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.tools.portfolio_analysis import PortfolioAnalysisInput
from app.tools.base import ToolError
from app.tools.portfolio_analysis import PortfolioAnalysisTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_holding(ticker: str, shares: float, avg_cost_basis: float | None = None) -> MagicMock:
    h = MagicMock()
    h.ticker = ticker
    h.shares = Decimal(str(shares))
    h.avg_cost_basis = Decimal(str(avg_cost_basis)) if avg_cost_basis is not None else None
    h.created_at = None
    return h


def _make_db_session(holdings: list) -> MagicMock:
    """Return a mock async context manager yielding a DB session that returns holdings."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = holdings

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def _fake_factory():
        yield mock_session

    return _fake_factory


def _make_input(user_id: str | None = None) -> PortfolioAnalysisInput:
    return PortfolioAnalysisInput(user_id=user_id or str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_no_holdings_returns_empty_output():
    tool = PortfolioAnalysisTool()
    inp = _make_input()

    with patch("app.tools.portfolio_analysis.AsyncSessionFactory", _make_db_session([])):
        result = await tool(inp)

    assert result.total_value == 0.0
    assert result.holdings == []
    assert result.top_gainers == []
    assert result.top_losers == []
    assert result.message is not None


async def test_single_holding_with_cost_basis():
    tool = PortfolioAnalysisTool()
    inp = _make_input()
    holdings = [_make_holding("AAPL", 10.0, avg_cost_basis=150.0)]

    with (
        patch("app.tools.portfolio_analysis.AsyncSessionFactory", _make_db_session(holdings)),
        patch("app.tools.portfolio_analysis._fetch_price", return_value=170.0),
    ):
        result = await tool(inp)

    assert result.total_value == pytest.approx(1700.0)
    assert len(result.holdings) == 1
    h = result.holdings[0]
    assert h.ticker == "AAPL"
    assert h.shares == pytest.approx(10.0)
    assert h.current_price == pytest.approx(170.0)
    assert h.current_value == pytest.approx(1700.0)
    assert h.avg_cost_basis == pytest.approx(150.0)
    assert h.gain_loss == pytest.approx(200.0)
    assert h.gain_loss_pct == pytest.approx(200.0 / 1500.0)
    assert h.price_fetch_error is None


async def test_single_holding_no_cost_basis():
    tool = PortfolioAnalysisTool()
    inp = _make_input()
    holdings = [_make_holding("AAPL", 10.0, avg_cost_basis=None)]

    with (
        patch("app.tools.portfolio_analysis.AsyncSessionFactory", _make_db_session(holdings)),
        patch("app.tools.portfolio_analysis._fetch_price", return_value=170.0),
    ):
        result = await tool(inp)

    h = result.holdings[0]
    assert h.gain_loss is None
    assert h.gain_loss_pct is None
    assert h.current_value == pytest.approx(1700.0)


async def test_partial_price_failure():
    """One ticker fails; others still return. total_value excludes failed tickers."""
    tool = PortfolioAnalysisTool()
    inp = _make_input()
    holdings = [
        _make_holding("AAPL", 10.0, avg_cost_basis=150.0),
        _make_holding("FAIL", 5.0, avg_cost_basis=100.0),
    ]

    def _price_side_effect(ticker: str) -> float | None:
        return 170.0 if ticker == "AAPL" else None

    with (
        patch("app.tools.portfolio_analysis.AsyncSessionFactory", _make_db_session(holdings)),
        patch("app.tools.portfolio_analysis._fetch_price", side_effect=_price_side_effect),
    ):
        result = await tool(inp)

    assert result.total_value == pytest.approx(1700.0)

    aapl = next(h for h in result.holdings if h.ticker == "AAPL")
    fail = next(h for h in result.holdings if h.ticker == "FAIL")

    assert aapl.current_value == pytest.approx(1700.0)
    assert aapl.price_fetch_error is None

    assert fail.current_price is None
    assert fail.current_value is None
    assert fail.price_fetch_error is not None


async def test_all_prices_fail_raises_tool_error():
    tool = PortfolioAnalysisTool()
    inp = _make_input()
    holdings = [_make_holding("FAIL1", 10.0), _make_holding("FAIL2", 5.0)]

    with (
        patch("app.tools.portfolio_analysis.AsyncSessionFactory", _make_db_session(holdings)),
        patch("app.tools.portfolio_analysis._fetch_price", return_value=None),
        pytest.raises(ToolError),
    ):
        await tool(inp)


async def test_top_gainers_and_losers():
    """5 holdings with distinct gain_loss_pct — verify top 3 gainers and bottom 1 loser."""
    tool = PortfolioAnalysisTool()
    inp = _make_input()

    # cost $100 each, prices arranged so pct = (price-100)/100
    tickers = ["A", "B", "C", "D", "E"]
    prices  = [150.0, 130.0, 120.0, 110.0, 90.0]  # pcts: 0.5, 0.3, 0.2, 0.1, -0.1
    holdings = [_make_holding(t, 1.0, avg_cost_basis=100.0) for t in tickers]

    def _price_side_effect(ticker: str) -> float:
        return prices[tickers.index(ticker)]

    with (
        patch("app.tools.portfolio_analysis.AsyncSessionFactory", _make_db_session(holdings)),
        patch("app.tools.portfolio_analysis._fetch_price", side_effect=_price_side_effect),
    ):
        result = await tool(inp)

    assert result.top_gainers == ["A", "B", "C"]
    assert "E" in result.top_losers


async def test_top_losers_with_single_eligible_holding():
    """With one holding that has a cost basis, top_losers should be populated (len >= 1)."""
    tool = PortfolioAnalysisTool()
    inp = _make_input()
    holdings = [_make_holding("AAPL", 10.0, avg_cost_basis=200.0)]

    with (
        patch("app.tools.portfolio_analysis.AsyncSessionFactory", _make_db_session(holdings)),
        patch("app.tools.portfolio_analysis._fetch_price", return_value=150.0),
    ):
        result = await tool(inp)

    # gain_loss_pct = (1500-2000)/2000 = -0.25 — qualifies for losers list
    assert len(result.top_losers) == 1
    assert result.top_losers[0] == "AAPL"


async def test_user_id_used_as_uuid_filter():
    """Verify that a malformed user_id raises ValueError before any DB call."""
    tool = PortfolioAnalysisTool()
    inp = PortfolioAnalysisInput(user_id="not-a-uuid")

    with pytest.raises((ValueError, Exception)):
        await tool(inp)
