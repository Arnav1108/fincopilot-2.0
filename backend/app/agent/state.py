"""
Agent state contract for the LangGraph financial research agent.

IMPORTANT — serialization constraint:
    All values stored in AgentState must be plain JSON-serializable Python
    primitives (str, int, float, bool, None, list, dict).  No ORM model
    instances, no Pydantic model instances, no uuid.UUID objects, and no
    datetime objects may appear inside AgentState at runtime.  Callers must
    convert UUIDs with str(...) and datetimes with .isoformat() before
    populating state.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict

import openai
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message


# ---------------------------------------------------------------------------
# Sub-TypedDicts
# ---------------------------------------------------------------------------

class PlanStep(TypedDict):
    id: str
    tool_name: str
    input_template: str
    dependencies: list[str]


class RecentMessage(TypedDict):
    role: str
    content: str


class ChunkDict(TypedDict):
    content: str
    metadata: dict | None
    score: float


# ---------------------------------------------------------------------------
# AgentState
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    user_id: str
    conversation_id: str
    analyst_profile: dict
    query: str
    classification: Literal["simple", "complex", "ingest"]
    plan: list[PlanStep]
    tool_results: dict[str, Any]
    retrieved_chunks: list[ChunkDict]
    reranked_chunks: list[ChunkDict]
    retrieval_quality_score: float
    rag_used: bool
    relevance_score: float | None
    retry_count: int
    conversation_summary: str
    recent_messages: list[RecentMessage]
    final_output: str
    error: str | None
    model: str
    has_uploaded_documents: bool


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------

class MemoryManager:
    async def load_memory(
        self,
        db: AsyncSession,
        user_id: str,
        conversation_id: str,
    ) -> dict:
        logger = structlog.get_logger(__name__)
        conv_id = uuid.UUID(conversation_id)
        u_id = uuid.UUID(user_id)

        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conv_id,
                Conversation.user_id == u_id,
                Conversation.deleted_at.is_(None),
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise ValueError("conversation not found")

        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(3)
        )
        messages = list(reversed(result.scalars().all()))

        recent = [{"role": m.role.value, "content": m.content} for m in messages]
        logger.debug(
            "memory_loaded",
            conversation_id=conversation_id,
            message_count=len(recent),
            has_summary=bool(conversation.rolling_summary),
        )
        return {
            "conversation_summary": conversation.rolling_summary or "",
            "recent_messages": recent,
        }

    async def regenerate_summary(
        self,
        prior_summary: str,
        new_messages: list[RecentMessage],
        openai_client: openai.AsyncOpenAI,
    ) -> str:
        logger = structlog.get_logger(__name__)
        if not new_messages:
            return prior_summary

        system_prompt = (
            "You are a financial research assistant. "
            "Given the prior conversation summary and new messages, "
            "produce a rolling summary in 300 words or fewer. "
            "Focus on tickers, research questions, key findings, and analyst preferences."
        )

        parts: list[str] = []
        if prior_summary:
            parts.append(f"Prior summary:\n{prior_summary}\n")
        parts.append("New messages:")
        for msg in new_messages:
            parts.append(f"{msg['role']}: {msg['content']}")
        user_message = "\n".join(parts)

        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        summary = response.choices[0].message.content.strip()
        logger.debug("summary_regenerated", word_count=len(summary.split()))
        return summary
