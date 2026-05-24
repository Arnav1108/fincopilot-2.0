from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.schemas.document import ChunkResult


class DocumentRetrievalInput(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000)
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    top_k: int = Field(5, ge=1, le=20)
    ticker: str | None = None
    doc_type: str | None = None
    fiscal_year: int | None = None


class DocumentRetrievalOutput(BaseModel):
    chunks: list[ChunkResult]
