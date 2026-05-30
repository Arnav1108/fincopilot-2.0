from __future__ import annotations

import json
import uuid

import openai
import structlog
from celery.exceptions import Retry
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from app.celery_app import celery_app
from app.config import settings
from app.models.conversation import Message
from app.models.memory import UserMemory

_SYNC_DATABASE_URL = settings.DATABASE_URL.replace(
    "postgresql+asyncpg://", "postgresql+psycopg2://"
)
_engine = create_engine(_SYNC_DATABASE_URL, pool_pre_ping=True)
_SessionLocal = sessionmaker(bind=_engine)

_task_log = structlog.get_logger(__name__)

_ALLOWED_FACT_TYPES = frozenset(
    {"ticker_interest", "sector_interest", "investment_style", "research_pattern"}
)

_SYSTEM_PROMPT = (
    "You are a financial research assistant. "
    "Your task is to extract key facts from a conversation turn that would "
    "meaningfully personalise future responses for this analyst. "
    "Only extract facts that are genuinely informative — do not extract trivial "
    "or generic observations.\n\n"
    "Return ONLY a JSON array of objects with exactly two keys:\n"
    '  "fact_type": one of ticker_interest | sector_interest | investment_style | research_pattern\n'
    '  "content": a concise plain-text description (max 200 characters)\n\n'
    "If there is nothing notable to extract, return an empty array: []\n\n"
    "IMPORTANT: Do NOT include names, email addresses, phone numbers, physical "
    "addresses, account numbers, or any other personally identifiable information "
    "in the content field."
)


@celery_app.task(
    bind=True,
    max_retries=2,
    name="app.tasks.memory_extraction.extract_memories",
)
def extract_memories(self, user_id: str, conversation_id: str) -> None:
    log = _task_log.bind(user_id=user_id, conversation_id=conversation_id)
    log.debug("memory_extraction_started")

    db = _SessionLocal()
    try:
        # 1. Read last 2 messages (most recent user + assistant pair)
        messages = (
            db.query(Message)
            .filter(Message.conversation_id == uuid.UUID(conversation_id))
            .order_by(Message.created_at.desc())
            .limit(2)
            .all()
        )
        messages = list(reversed(messages))

        if not messages:
            log.debug("memory_extraction_empty")
            return

        # 2. Format messages for the extraction prompt
        formatted = "\n".join(
            f"{m.role.value}: {m.content}" for m in messages
        )

        # 3. Call GPT-4o-mini for extraction
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=settings.MEMORY_EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": formatted},
            ],
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()

        # 4. Parse JSON response
        try:
            facts = json.loads(raw)
            if not isinstance(facts, list):
                raise ValueError("expected a JSON array")
        except (json.JSONDecodeError, ValueError):
            log.warning(
                "memory_extraction_parse_failed",
                raw_response=raw[:200],
            )
            return

        if not facts:
            log.debug("memory_extraction_empty")
            return

        # 5. Validate and clean each fact
        valid_facts: list[dict] = []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            fact_type = fact.get("fact_type", "")
            content = fact.get("content", "")

            if fact_type not in _ALLOWED_FACT_TYPES:
                log.warning(
                    "memory_extraction_unknown_fact_type",
                    fact_type=fact_type,
                )
                continue

            if not content or not str(content).strip():
                continue

            # Truncate to 200 chars as required
            content = str(content).strip()[:200]
            valid_facts.append({"fact_type": fact_type, "content": content})

        if not valid_facts:
            log.debug("memory_extraction_empty")
            return

        # 6. Eviction: delete oldest rows to stay within the cap
        u_id = uuid.UUID(user_id)
        conv_id = uuid.UUID(conversation_id)

        try:
            current_count = (
                db.query(func.count(UserMemory.id))
                .filter(UserMemory.user_id == u_id)
                .scalar()
            ) or 0
            n_evict = max(
                0,
                current_count + len(valid_facts) - settings.USER_MEMORY_MAX_COUNT,
            )
            if n_evict > 0:
                oldest = (
                    db.query(UserMemory)
                    .filter(UserMemory.user_id == u_id)
                    .order_by(UserMemory.created_at.asc())
                    .limit(n_evict)
                    .all()
                )
                for row in oldest:
                    db.delete(row)
                db.flush()

            # 7. Bulk insert new facts
            db.add_all(
                [
                    UserMemory(
                        user_id=u_id,
                        conversation_id=conv_id,
                        fact_type=f["fact_type"],
                        content=f["content"],
                    )
                    for f in valid_facts
                ]
            )
            db.commit()

        except Retry:
            raise
        except Exception as db_err:
            db.rollback()
            log.error(
                "memory_extraction_db_error",
                error=str(db_err),
                retry_count=self.request.retries,
            )
            raise self.retry(exc=db_err, countdown=10)

        log.info(
            "memory_extraction_completed",
            facts_extracted=len(valid_facts),
            facts_evicted=n_evict,
        )

    except Retry:
        raise
    except Exception as exc:
        log.error("memory_extraction_db_error", error=str(exc), retry_count=self.request.retries)
        raise self.retry(exc=exc, countdown=10)
    finally:
        db.close()
