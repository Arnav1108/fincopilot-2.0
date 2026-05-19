from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import clerk_auth
from app.database import get_db
from app.models.user import AnalystProfile, User
from app.schemas.profile import ProfileRead, ProfileUpdate

router = APIRouter()
logger = structlog.get_logger(__name__)

_FIELD_MAP = {"sectors": "sectors_of_interest"}


async def _get_or_create_profile(
    user: User, db: AsyncSession
) -> tuple[AnalystProfile, bool]:
    result = await db.execute(
        select(AnalystProfile).where(AnalystProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is not None:
        return profile, False

    profile = AnalystProfile(user_id=user.id)
    db.add(profile)
    try:
        await db.commit()
        await db.refresh(profile)
        return profile, True
    except IntegrityError:
        await db.rollback()
        result = await db.execute(
            select(AnalystProfile).where(AnalystProfile.user_id == user.id)
        )
        return result.scalar_one(), False


@router.get("/", response_model=ProfileRead)
async def get_profile(
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    profile, created = await _get_or_create_profile(user, db)
    if created:
        logger.info("profile_created", user_id=str(user.id), profile_id=str(profile.id))
    else:
        logger.debug("profile_fetched", user_id=str(user.id), profile_id=str(profile.id))
    return ProfileRead.model_validate(profile)


@router.put("/", response_model=ProfileRead)
async def update_profile(
    body: ProfileUpdate,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    profile, created = await _get_or_create_profile(user, db)
    if created:
        logger.info("profile_created", user_id=str(user.id), profile_id=str(profile.id))

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, _FIELD_MAP.get(field, field), value)
    profile.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(profile)
    logger.info("profile_updated", user_id=str(user.id), profile_id=str(profile.id))
    return ProfileRead.model_validate(profile)
