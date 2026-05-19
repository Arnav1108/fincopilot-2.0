import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update

from app.api.auth import clerk_auth
from app.database import AsyncSessionFactory
from app.models.conversation import Conversation, Message, MessageRole
from app.models.user import User
from app.schemas.chat import ChatRequest

router = APIRouter()
logger = structlog.get_logger(__name__)

CANNED_RESPONSE = (
    "Based on Apple's 2023 10-K filing, total net sales were $383.3 billion, "
    "a 3% decline from fiscal 2022. iPhone revenue contributed $200.6 billion, "
    "representing 52% of total revenue. Services revenue grew 9% year-over-year "
    "to $85.2 billion, now the second-largest segment. Gross margin improved to "
    "44.1% despite lower overall revenue, driven by the higher-margin Services mix. "
    "The company held $162 billion in cash and marketable securities, with operating "
    "cash flow of $114 billion — one of the strongest cash generation profiles in "
    "the S&P 500."
)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_events(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    yield _sse("node_update", {"node": "Routing", "status": "running"})
    await asyncio.sleep(0.3)
    yield _sse("node_update", {"node": "Planning", "status": "running"})
    await asyncio.sleep(0.5)
    yield _sse("node_update", {"node": "Executing", "status": "running"})
    yield _sse("tool_call", {"tool": "search_sec_filings", "input": {"query": "AAPL 10-K 2023"}})
    await asyncio.sleep(0.2)
    yield _sse("sources", {"sources": [
        {"title": "Apple 10-K 2023", "url": "https://sec.gov/fake/aapl-10k-2023"},
        {"title": "Apple Q4 Earnings", "url": "https://sec.gov/fake/aapl-q4-2023"},
    ]})
    await asyncio.sleep(0.1)
    yield _sse("node_update", {"node": "Synthesizing", "status": "running"})

    for word in CANNED_RESPONSE.split():
        yield _sse("token", {"token": word + " "})
        await asyncio.sleep(0.08)

    assistant_message_id = None
    try:
        async with AsyncSessionFactory() as db:
            msg = Message(
                conversation_id=conversation_id,
                role=MessageRole.assistant,
                content=CANNED_RESPONSE,
            )
            db.add(msg)
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
            await db.refresh(msg)
            assistant_message_id = msg.id
            logger.info(
                "chat_stream_completed",
                conversation_id=str(conversation_id),
                user_id=str(user_id),
                assistant_message_id=str(assistant_message_id),
            )
    except Exception:
        logger.error(
            "assistant_message_save_failed",
            conversation_id=str(conversation_id),
            user_id=str(user_id),
        )

    yield _sse("done", {"message_id": str(assistant_message_id) if assistant_message_id else None})


@router.post("/{conversation_id}/stream")
async def stream_chat(
    conversation_id: uuid.UUID,
    body: ChatRequest,
    user: User = Depends(clerk_auth),
):
    if body.conversation_id != conversation_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="conversation_id mismatch")

    async with AsyncSessionFactory() as db:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id,
                Conversation.deleted_at.is_(None),
            )
        )
        if result.scalar_one_or_none() is None:
            logger.warning(
                "conversation_not_found",
                conversation_id=str(conversation_id),
                user_id=str(user.id),
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

        msg = Message(
            conversation_id=conversation_id,
            role=MessageRole.user,
            content=body.message,
        )
        db.add(msg)
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=datetime.now(timezone.utc))
        )
        await db.commit()
        logger.info(
            "user_message_saved",
            conversation_id=str(conversation_id),
            user_id=str(user.id),
        )

    logger.info("chat_stream_started", conversation_id=str(conversation_id), user_id=str(user.id))

    return StreamingResponse(
        _stream_events(conversation_id, user.id),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
