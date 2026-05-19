from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InvestmentStyle(str, Enum):
    growth = "growth"
    value = "value"
    blend = "blend"


class OutputFormat(str, Enum):
    concise = "concise"
    detailed = "detailed"
    bullet_points = "bullet_points"


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    user_id: uuid.UUID
    sectors: list[str] = Field(default=[], validation_alias="sectors_of_interest")
    tracked_tickers: list[str] = Field(default=[])
    investment_style: Optional[InvestmentStyle] = None
    preferred_output_format: Optional[OutputFormat] = None
    custom_context: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("sectors", "tracked_tickers", mode="before")
    @classmethod
    def coerce_none_to_empty_list(cls, v: list | None) -> list:
        return v if v is not None else []


class ProfileUpdate(BaseModel):
    sectors: Optional[list[str]] = None
    tracked_tickers: Optional[list[str]] = None
    investment_style: Optional[InvestmentStyle] = None
    preferred_output_format: Optional[OutputFormat] = None
    custom_context: Optional[str] = None

    @field_validator("sectors")
    @classmethod
    def validate_sectors(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 50:
            raise ValueError("sectors may contain at most 50 items")
        for item in v:
            if not (1 <= len(item) <= 100):
                raise ValueError("each sector must be 1–100 characters")
        return v

    @field_validator("tracked_tickers")
    @classmethod
    def validate_tickers(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 100:
            raise ValueError("tracked_tickers may contain at most 100 items")
        for ticker in v:
            if not re.match(r"^[A-Z]{1,10}$", ticker):
                raise ValueError(f"invalid ticker symbol: {ticker!r}")
        return v

    @field_validator("custom_context")
    @classmethod
    def strip_and_validate_context(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if len(stripped) > 500:
            raise ValueError("custom_context must be ≤ 500 characters")
        return stripped
