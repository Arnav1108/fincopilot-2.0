from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DocumentFinderInput(BaseModel):
    ticker: str
    filing_type: Literal["10-K", "10-Q", "transcript", "presentation", "other"] = "10-K"
    conversation_id: str
    user_id: str | None = None
    query: str | None = Field(default=None, max_length=300)


class DocumentFinderOutput(BaseModel):
    status: Literal["ready", "failed", "duplicate", "cancelled"]
    chunk_count: int | None = None
    document_id: str | None = None
    message: str | None = None
