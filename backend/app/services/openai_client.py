from __future__ import annotations

import structlog
from openai import AsyncOpenAI

from app.config import settings

logger = structlog.get_logger(__name__)

openai_client: AsyncOpenAI = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

logger.debug("openai_client_initialized")
