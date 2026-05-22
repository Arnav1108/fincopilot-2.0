from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID
    message: str = Field(..., max_length=10_000)
    model: str = Field("gpt-4o")

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v
