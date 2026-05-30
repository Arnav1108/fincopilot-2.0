import structlog
from fastapi import APIRouter, Depends, Request

from app.api.auth import clerk_auth
from app.models.user import User

logger = structlog.get_logger()

router = APIRouter()


@router.get("/health")
async def health():
    logger.debug("health check", endpoint="health")
    return {"status": "ok", "version": "2.0.0"}


@router.get("/health/authed")
async def authed_health(request: Request, user: User = Depends(clerk_auth)):
    return {"status": "ok", "user_id": str(request.state.user.id)}
