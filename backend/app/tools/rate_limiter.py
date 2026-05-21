from __future__ import annotations

import asyncio
import time

import redis.asyncio as aioredis
import structlog

from app.config import settings
from app.tools.base import ToolRateLimitError

logger = structlog.get_logger(__name__)

_RATE_LIMIT_LUA = """
local key = KEYS[1]
local count = redis.call('INCR', key)
redis.call('EXPIRE', key, 2)
return count
"""

_MAX_TOKENS_PER_SECOND = 10
_RETRY_INTERVAL = 0.1
_MAX_WAIT = 5.0


class RedisTokenBucket:
    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None
        self._script_sha: str | None = None

    def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._client

    async def _load_script(self, client: aioredis.Redis) -> str:
        if self._script_sha is None:
            self._script_sha = await client.script_load(_RATE_LIMIT_LUA)
        return self._script_sha

    async def acquire(self) -> None:
        client = self._get_client()
        sha = await self._load_script(client)
        deadline = time.monotonic() + _MAX_WAIT
        attempt = 0

        while True:
            epoch_second = int(time.time())
            key = f"edgar_ratelimit:{epoch_second}"
            count = await client.evalsha(sha, 1, key)

            if count <= _MAX_TOKENS_PER_SECOND:
                logger.debug("rate_limiter_token_acquired", epoch_second=epoch_second, count=count)
                return

            attempt += 1
            logger.warning("rate_limiter_waiting", epoch_second=epoch_second, wait_attempt=attempt)

            if time.monotonic() >= deadline:
                raise ToolRateLimitError(
                    f"EDGAR rate limit exceeded: {count} requests in current second"
                )

            await asyncio.sleep(_RETRY_INTERVAL)


edgar_rate_limiter = RedisTokenBucket()
