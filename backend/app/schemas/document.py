from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentUploadResponse(BaseModel):
    document_id: uuid.UUID
    status: str


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    filename: str
    doc_type: str
    ticker: str | None
    filing_date: date | None
    status: str
    chunk_count: int | None
    error_message: str | None
    created_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentRead]


class RetrieveDebugRequest(BaseModel):
    conversation_id: uuid.UUID
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)


class ChunkResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    similarity_score: float
    content: str
    metadata: dict | None


class RetrieveDebugResponse(BaseModel):
    results: list[ChunkResult]


class ChunkRead(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    conversation_id: uuid.UUID
    chunk_index: int
    content: str
    metadata: dict | None
    created_at: datetime
