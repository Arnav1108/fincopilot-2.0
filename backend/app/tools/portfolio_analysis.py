from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionFactory
from app.models.portfolio import Portfolio, PortfolioHolding
from app.schemas.tools.portfolio_analysis import (
    HoldingResult,
    PortfolioAnalysisInput,
    PortfolioAnalysisOutput,
)
from app.tools.base import BaseTool, ToolError

logger = structlog.get_logger(__name__)


def _fetch_price(ticker: str) -> float | None:
    import yfinance as yf

    try:
        fast = yf.Ticker(ticker).fast_info
        price = getattr(fast, "last_price", None)
        return float(price) if price is not None else None
    except Exception:
        return None


class PortfolioAnalysisTool(BaseTool[PortfolioAnalysisInput, PortfolioAnalysisOutput]):
    async def __call__(self, input: PortfolioAnalysisInput) -> PortfolioAnalysisOutput:  # noqa: A002
        user_uuid = uuid.UUID(input.user_id)

        async with AsyncSessionFactory() as db:
            result = await db.execute(
                select(PortfolioHolding)
                .join(Portfolio, PortfolioHolding.portfolio_id == Portfolio.id)
                .where(Portfolio.user_id == user_uuid)
                .order_by(PortfolioHolding.created_at.asc())
            )
            rows = result.scalars().all()

        holding_count = len(rows)
        logger.debug(
            "tool_called",
            tool_name="portfolio_analysis",
            user_id=input.user_id,
            holding_count=holding_count,
        )

        if not rows:
            return PortfolioAnalysisOutput(
                total_value=0.0,
                holdings=[],
                top_gainers=[],
                top_losers=[],
                message="No portfolio holdings found. Add holdings via the portfolio API.",
            )

        unique_tickers = list({r.ticker for r in rows})
        semaphore = asyncio.Semaphore(4)
        loop = asyncio.get_running_loop()

        async def _fetch_one(ticker: str) -> tuple[str, float | None, str | None]:
            async with semaphore:
                try:
                    price = await loop.run_in_executor(None, _fetch_price, ticker)
                    if price is None:
                        return ticker, None, "price unavailable from yfinance"
                    return ticker, price, None
                except Exception as e:
                    return ticker, None, str(e)

        price_results = await asyncio.gather(*[_fetch_one(t) for t in unique_tickers])

        price_map: dict[str, float | None] = {}
        error_map: dict[str, str | None] = {}
        for ticker, price, err in price_results:
            price_map[ticker] = price
            error_map[ticker] = err
            if err:
                logger.warning(
                    "portfolio_price_fetch_failed", ticker=ticker, error=err
                )

        if all(p is None for p in price_map.values()):
            logger.error(
                "portfolio_all_prices_failed",
                user_id=input.user_id,
                ticker_count=len(unique_tickers),
            )
            raise ToolError(
                "Could not fetch prices for any portfolio holdings. yfinance may be unavailable."
            )

        holding_results: list[HoldingResult] = []
        total_value = 0.0

        for row in rows:
            shares = float(row.shares)
            cost = float(row.avg_cost_basis) if row.avg_cost_basis is not None else None
            price = price_map.get(row.ticker)
            err = error_map.get(row.ticker)

            current_value = shares * price if price is not None else None
            if current_value is not None:
                total_value += current_value

            gain_loss = (
                (current_value - shares * cost)
                if (current_value is not None and cost is not None)
                else None
            )
            gain_loss_pct = (
                (gain_loss / (shares * cost))
                if (gain_loss is not None and cost is not None and cost > 0)
                else None
            )

            holding_results.append(
                HoldingResult(
                    ticker=row.ticker,
                    shares=shares,
                    current_price=price,
                    current_value=current_value,
                    avg_cost_basis=cost,
                    gain_loss=gain_loss,
                    gain_loss_pct=gain_loss_pct,
                    price_fetch_error=err,
                )
            )

        ranked = sorted(
            [h for h in holding_results if h.gain_loss_pct is not None],
            key=lambda h: h.gain_loss_pct,
            reverse=True,
        )
        top_gainers = [h.ticker for h in ranked[:3]]
        top_losers = [h.ticker for h in ranked[-3:][::-1]] if len(ranked) >= 1 else []

        logger.debug(
            "tool_succeeded",
            tool_name="portfolio_analysis",
            user_id=input.user_id,
            total_value=total_value,
            holding_count=holding_count,
        )
        return PortfolioAnalysisOutput(
            total_value=total_value,
            holdings=holding_results,
            top_gainers=top_gainers,
            top_losers=top_losers,
        )
