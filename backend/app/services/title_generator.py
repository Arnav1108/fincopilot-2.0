from __future__ import annotations

import re

import structlog
from openai import AsyncOpenAI

from app.config import settings

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a conversation title generator for a financial research platform. Given the user's "
    "first message, return a specific 5-7 word title that captures the exact company, metric, "
    "time period, and action mentioned. Rules: include the ticker or company name if present, "
    "include the fiscal year or quarter if mentioned, include the specific metric (revenue, margin, "
    "EPS, etc.) if mentioned, use title case, no punctuation, no quotes, no filler words like "
    "'Analysis' or 'Overview' or 'Discussion'. Examples: 'AAPL FY2024 Gross Margin Breakdown', "
    "'MSFT Q3 2025 Cloud Revenue Growth', 'Tesla 2024 Free Cash Flow Trends', "
    "'Compare Google Meta Ad Revenue 2024'. Return only the title, nothing else."
)


async def generate_title(message: str) -> str:
    try:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            max_tokens=30,
            temperature=0.3,
        )
        raw = (response.choices[0].message.content or "").strip()
        title = re.sub(r'^["\'\s]+|["\'\s.,!?;:]+$', "", raw).strip()
        logger.debug("title_generated", message_length=len(message), title=title)
        return title
    except Exception as exc:
        logger.warning("title_generation_failed", error=str(exc))
        return ""
