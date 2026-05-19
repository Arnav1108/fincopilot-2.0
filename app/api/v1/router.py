from fastapi import APIRouter

from app.api.v1 import conversations, health, profile, webhooks

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(webhooks.router, tags=["webhooks"])
router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
router.include_router(profile.router, prefix="/profile", tags=["profile"])
