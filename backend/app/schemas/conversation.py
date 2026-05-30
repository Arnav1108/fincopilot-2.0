from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    @field_validator("title", mode="before")
    @classmethod
    def coerce_null_title(cls, v: str | None) -> str:
        return v if v is not None else "New Conversation"


class ConversationUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("title cannot be blank")
        return stripped


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    rag_used: bool = False
    relevance_score: Optional[float] = None
    retrieved_chunk_ids: Optional[list[str]] = None
    chart_data: Optional[dict] = None
