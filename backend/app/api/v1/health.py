from fastapi import APIRouter, Depends, Request

from app.api.auth import clerk_auth
from app.models.user import User

router = APIRouter()


@router.get("/health/authed")
async def authed_health(request: Request, user: User = Depends(clerk_auth)):
    return {"status": "ok", "user_id": str(request.state.user.id)}
