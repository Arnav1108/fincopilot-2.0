from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PortfolioCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class HoldingCreate(BaseModel):
    ticker: str = Field(..., pattern=r"^[A-Z0-9]{1,10}$")
    shares: Decimal = Field(..., gt=0)
    avg_cost_basis: Optional[Decimal] = Field(None, gt=0)


class HoldingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    portfolio_id: uuid.UUID
    ticker: str
    shares: Decimal
    avg_cost_basis: Optional[Decimal]
    created_at: datetime
    updated_at: datetime


class PortfolioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime
    holdings: list[HoldingResponse] = []
