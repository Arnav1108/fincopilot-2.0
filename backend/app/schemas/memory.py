from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fact_type: str
    content: str
    conversation_id: uuid.UUID | None
    created_at: datetime


class MemoryListResponse(BaseModel):
    memories: list[MemoryRead]
    count: int
