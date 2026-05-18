from fastapi import APIRouter

from app.api.v1 import health, webhooks

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(webhooks.router, tags=["webhooks"])
