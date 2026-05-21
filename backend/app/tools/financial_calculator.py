from __future__ import annotations

import structlog

from app.schemas.tools.financial_calculator import FinancialCalculatorInput, FinancialCalculatorOutput
from app.tools.base import BaseTool

logger = structlog.get_logger(__name__)


class FinancialCalculatorTool(BaseTool[FinancialCalculatorInput, FinancialCalculatorOutput]):
    async def __call__(self, input: FinancialCalculatorInput) -> FinancialCalculatorOutput:  # noqa: A002
        logger.debug("tool_called", tool_name="financial_calculator")
        errors: list[str] = []

        pe_ratio = self._div(input.current_price, input.eps_diluted, "pe_ratio", errors)
        ev_ebitda = self._ev_ebitda(input, errors)
        roe = self._div(input.net_income, input.total_equity, "roe", errors)
        debt_equity_ratio = self._div(input.total_debt, input.total_equity, "debt_equity_ratio", errors)
        revenue_growth_yoy = self._revenue_growth_yoy(input, errors)
        revenue_growth_3yr_cagr = self._revenue_growth_3yr_cagr(input, errors)
        gross_margin = self._div(input.gross_profit, input.revenue_current, "gross_margin", errors)
        operating_margin = self._div(input.operating_income, input.revenue_current, "operating_margin", errors)
        net_margin = self._div(input.net_income, input.revenue_current, "net_margin", errors)

        out = FinancialCalculatorOutput(
            pe_ratio=pe_ratio,
            ev_ebitda=ev_ebitda,
            roe=roe,
            debt_equity_ratio=debt_equity_ratio,
            revenue_growth_yoy=revenue_growth_yoy,
            revenue_growth_3yr_cagr=revenue_growth_3yr_cagr,
            gross_margin=gross_margin,
            operating_margin=operating_margin,
            net_margin=net_margin,
            errors=errors,
        )
        logger.debug("tool_succeeded", tool_name="financial_calculator", errors=errors)
        return out

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _div(
        numerator: float | None,
        denominator: float | None,
        name: str,
        errors: list[str],
    ) -> float | None:
        if numerator is None:
            errors.append(f"{name}: numerator is None")
            return None
        if denominator is None:
            errors.append(f"{name}: denominator is None")
            return None
        if denominator == 0:
            errors.append(f"{name}: denominator is zero")
            return None
        return numerator / denominator

    @staticmethod
    def _ev_ebitda(
        inp: FinancialCalculatorInput, errors: list[str]
    ) -> float | None:
        for field, label in [
            (inp.market_cap, "market_cap"),
            (inp.total_debt, "total_debt"),
            (inp.cash_and_equivalents, "cash_and_equivalents"),
            (inp.ebitda, "ebitda"),
        ]:
            if field is None:
                errors.append(f"ev_ebitda: {label} is None")
                return None
        if inp.ebitda == 0:
            errors.append("ev_ebitda: denominator is zero")
            return None
        return (inp.market_cap + inp.total_debt - inp.cash_and_equivalents) / inp.ebitda  # type: ignore[operator]

    @staticmethod
    def _revenue_growth_yoy(
        inp: FinancialCalculatorInput, errors: list[str]
    ) -> float | None:
        if inp.revenue_current is None:
            errors.append("revenue_growth_yoy: revenue_current is None")
            return None
        if inp.revenue_prior_year is None:
            errors.append("revenue_growth_yoy: revenue_prior_year is None")
            return None
        if inp.revenue_prior_year == 0:
            errors.append("revenue_growth_yoy: denominator is zero")
            return None
        return (inp.revenue_current - inp.revenue_prior_year) / abs(inp.revenue_prior_year)

    @staticmethod
    def _revenue_growth_3yr_cagr(
        inp: FinancialCalculatorInput, errors: list[str]
    ) -> float | None:
        if inp.revenue_current is None:
            errors.append("revenue_growth_3yr_cagr: revenue_current is None")
            return None
        if inp.revenue_3yr_ago is None:
            errors.append("revenue_growth_3yr_cagr: revenue_3yr_ago is None")
            return None
        if inp.revenue_3yr_ago == 0:
            errors.append("revenue_growth_3yr_cagr: denominator is zero")
            return None
        return (inp.revenue_current / inp.revenue_3yr_ago) ** (1 / 3) - 1
