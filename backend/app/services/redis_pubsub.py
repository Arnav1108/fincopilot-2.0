from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis

from app.config import settings


def get_async_redis() -> aioredis.Redis:
    return aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


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
