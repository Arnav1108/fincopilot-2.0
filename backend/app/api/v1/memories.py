from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import clerk_auth
from app.database import get_db
from app.models.memory import UserMemory
from app.models.user import User
from app.schemas.memory import MemoryListResponse, MemoryRead

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get("", response_model=MemoryListResponse)
async def get_memories(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(clerk_auth),
) -> MemoryListResponse:
    result = await db.execute(
        select(UserMemory)
        .where(UserMemory.user_id == user.id)
        .order_by(UserMemory.created_at.asc())
    )
    rows = result.scalars().all()
    return MemoryListResponse(
        memories=[MemoryRead.model_validate(r) for r in rows],
        count=len(rows),
    )


@router.delete("", status_code=204, response_class=Response)
async def delete_memories(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(clerk_auth),
) -> Response:
    count_result = await db.execute(
        select(func.count()).select_from(UserMemory).where(UserMemory.user_id == user.id)
    )
    rows_deleted = count_result.scalar_one()
    await db.execute(delete(UserMemory).where(UserMemory.user_id == user.id))
    await db.commit()
    logger.info("memories_cleared", user_id=str(user.id), rows_deleted=rows_deleted)
    return Response(status_code=204)
