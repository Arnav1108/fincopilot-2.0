import asyncio

import pytest

from app.schemas.tools.financial_calculator import FinancialCalculatorInput
from app.tools.financial_calculator import FinancialCalculatorTool

_TOOL = FinancialCalculatorTool()

_FULL_INPUT = FinancialCalculatorInput(
    current_price=150.0,
    market_cap=2_400_000_000_000.0,
    eps_diluted=6.0,
    ebitda=120_000_000_000.0,
    total_debt=100_000_000_000.0,
    cash_and_equivalents=50_000_000_000.0,
    total_equity=80_000_000_000.0,
    net_income=96_000_000_000.0,
    revenue_current=390_000_000_000.0,
    revenue_prior_year=360_000_000_000.0,
    revenue_3yr_ago=300_000_000_000.0,
    gross_profit=180_000_000_000.0,
    operating_income=110_000_000_000.0,
)


def run(coro):
    return asyncio.run(coro)


def test_all_ratios_correct():
    r = run(_TOOL(_FULL_INPUT))
    assert r.errors == []
    assert abs(r.pe_ratio - 25.0) < 0.001
    assert abs(r.ev_ebitda - (2_400 + 100 - 50) / 120) < 0.001
    assert abs(r.roe - (96 / 80)) < 0.001
    assert abs(r.debt_equity_ratio - (100 / 80)) < 0.001
    assert abs(r.revenue_growth_yoy - (30 / 360)) < 0.001
    assert abs(r.gross_margin - (180 / 390)) < 0.001
    assert abs(r.operating_margin - (110 / 390)) < 0.001
    assert abs(r.net_margin - (96 / 390)) < 0.001


def test_none_input_produces_none_and_error():
    inp = _FULL_INPUT.model_copy(update={"eps_diluted": None})
    r = run(_TOOL(inp))
    assert r.pe_ratio is None
    assert any("pe_ratio" in e for e in r.errors)


def test_division_by_zero_produces_none():
    inp = _FULL_INPUT.model_copy(update={"total_equity": 0.0})
    r = run(_TOOL(inp))
    assert r.roe is None
    assert any("denominator is zero" in e and "roe" in e for e in r.errors)
    assert r.debt_equity_ratio is None
    assert any("denominator is zero" in e and "debt_equity_ratio" in e for e in r.errors)


def test_all_none_inputs():
    r = run(_TOOL(FinancialCalculatorInput()))
    assert r.pe_ratio is None
    assert r.ev_ebitda is None
    assert r.roe is None
    assert r.debt_equity_ratio is None
    assert r.revenue_growth_yoy is None
    assert r.revenue_growth_3yr_cagr is None
    assert r.gross_margin is None
    assert r.operating_margin is None
    assert r.net_margin is None
    assert len(r.errors) == 9


def test_partial_inputs_compute_available_ratios():
    inp = FinancialCalculatorInput(
        revenue_current=390.0,
        gross_profit=180.0,
        operating_income=110.0,
        net_income=96.0,
    )
    r = run(_TOOL(inp))
    assert r.gross_margin is not None
    assert r.operating_margin is not None
    assert r.net_margin is not None
    assert r.pe_ratio is None
    assert r.ev_ebitda is None
