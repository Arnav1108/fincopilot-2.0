from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

_pool = aioredis.ConnectionPool.from_url(
    settings.REDIS_URL,
    max_connections=20,
    decode_responses=True,
)
logger.debug("redis_pool_initialized", max_connections=20)

_redis_client = aioredis.Redis(connection_pool=_pool)


def get_async_redis() -> aioredis.Redis:
    return _redis_client


@asynccontextmanager
async def subscribe_to_channel(channel: str) -> AsyncGenerator[aioredis.client.PubSub, None]:
    client = get_async_redis()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
        yield pubsub
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await client.aclose()
