from fastapi import APIRouter

from app.api.v1 import chat, conversations, documents, health, memories, portfolios, profile, webhooks

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(webhooks.router, tags=["webhooks"])
router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
router.include_router(chat.router, prefix="/conversations", tags=["chat"])
router.include_router(profile.router, prefix="/profile", tags=["profile"])
router.include_router(documents.router, prefix="/documents", tags=["documents"])
router.include_router(portfolios.router, prefix="/portfolios", tags=["portfolios"])
router.include_router(memories.router, prefix="/memories", tags=["memories"])
