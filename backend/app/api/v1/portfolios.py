from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import clerk_auth
from app.database import get_db
from app.models.portfolio import Portfolio, PortfolioHolding
from app.models.user import User
from app.schemas.portfolio import HoldingCreate, HoldingResponse, PortfolioCreate, PortfolioResponse

router = APIRouter()
logger = structlog.get_logger(__name__)


async def _get_portfolio_or_404(
    portfolio_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    *,
    load_holdings: bool = False,
) -> Portfolio:
    stmt = select(Portfolio).where(
        Portfolio.id == portfolio_id,
        Portfolio.user_id == user.id,
    )
    if load_holdings:
        stmt = stmt.options(selectinload(Portfolio.holdings))
    result = await db.execute(stmt)
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        logger.warning(
            "portfolio_not_found",
            user_id=str(user.id),
            portfolio_id=str(portfolio_id),
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not found")
    return portfolio


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=PortfolioResponse)
async def create_portfolio(
    body: PortfolioCreate,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
) -> PortfolioResponse:
    portfolio = Portfolio(user_id=user.id, name=body.name)
    db.add(portfolio)
    await db.commit()
    result = await db.execute(
        select(Portfolio)
        .where(Portfolio.id == portfolio.id)
        .options(selectinload(Portfolio.holdings))
    )
    portfolio = result.scalar_one()
    logger.debug("portfolio_create", user_id=str(user.id), portfolio_id=str(portfolio.id))
    return PortfolioResponse.model_validate(portfolio)


@router.get("/", response_model=list[PortfolioResponse])
async def list_portfolios(
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
) -> list[PortfolioResponse]:
    stmt = (
        select(Portfolio)
        .where(Portfolio.user_id == user.id)
        .options(selectinload(Portfolio.holdings))
        .order_by(Portfolio.created_at.desc())
    )
    result = await db.execute(stmt)
    portfolios = result.scalars().all()
    return [PortfolioResponse.model_validate(p) for p in portfolios]


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(
    portfolio_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
) -> PortfolioResponse:
    portfolio = await _get_portfolio_or_404(portfolio_id, user, db, load_holdings=True)
    return PortfolioResponse.model_validate(portfolio)


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_portfolio(
    portfolio_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    portfolio = await _get_portfolio_or_404(portfolio_id, user, db)
    await db.delete(portfolio)
    await db.commit()
    logger.debug("portfolio_delete", user_id=str(user.id), portfolio_id=str(portfolio_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{portfolio_id}/holdings",
    status_code=status.HTTP_201_CREATED,
    response_model=HoldingResponse,
)
async def add_holding(
    portfolio_id: uuid.UUID,
    body: HoldingCreate,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
) -> HoldingResponse:
    await _get_portfolio_or_404(portfolio_id, user, db)
    holding = PortfolioHolding(
        portfolio_id=portfolio_id,
        ticker=body.ticker,
        shares=body.shares,
        avg_cost_basis=body.avg_cost_basis,
    )
    db.add(holding)
    await db.commit()
    await db.refresh(holding)
    logger.debug(
        "holding_create",
        user_id=str(user.id),
        portfolio_id=str(portfolio_id),
        holding_id=str(holding.id),
        ticker=holding.ticker,
    )
    return HoldingResponse.model_validate(holding)


@router.get("/{portfolio_id}/holdings", response_model=list[HoldingResponse])
async def list_holdings(
    portfolio_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
) -> list[HoldingResponse]:
    await _get_portfolio_or_404(portfolio_id, user, db)
    stmt = (
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio_id)
        .order_by(PortfolioHolding.created_at.asc())
    )
    result = await db.execute(stmt)
    holdings = result.scalars().all()
    return [HoldingResponse.model_validate(h) for h in holdings]


@router.delete(
    "/{portfolio_id}/holdings/{holding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_holding(
    portfolio_id: uuid.UUID,
    holding_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _get_portfolio_or_404(portfolio_id, user, db)
    result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.id == holding_id,
            PortfolioHolding.portfolio_id == portfolio_id,
        )
    )
    holding = result.scalar_one_or_none()
    if holding is None:
        logger.warning(
            "holding_not_found",
            user_id=str(user.id),
            portfolio_id=str(portfolio_id),
            holding_id=str(holding_id),
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holding not found")
    await db.delete(holding)
    await db.commit()
    logger.debug(
        "holding_delete",
        user_id=str(user.id),
        portfolio_id=str(portfolio_id),
        holding_id=str(holding_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
