from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import openai
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.runnables import RunnableConfig
from sqlalchemy import func, select, update

from app.agent.graph import compiled_graph
from app.agent.state import AgentState, MemoryManager
from app.agent.stream_context import reset_stream_queue, set_stream_queue
from app.api.auth import clerk_auth
from app.config import settings
from app.database import AsyncSessionFactory
from app.models.conversation import Conversation, Message, MessageRole
from app.models.user import User
from app.schemas.chat import ChatRequest

router = APIRouter()
logger = structlog.get_logger(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_events(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    message: str,
    model: str,
    memory: dict,
) -> AsyncGenerator[str, None]:
    queue: asyncio.Queue = asyncio.Queue()
    token = set_stream_queue(queue)

    initial_state: AgentState = {
        "user_id": str(user_id),
        "conversation_id": str(conversation_id),
        "query": message,
        "model": model,
        "classification": "simple",
        "plan": [],
        "tool_results": {},
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "retrieval_quality_score": 1.0,
        "retry_count": 0,
        "conversation_summary": memory.get("conversation_summary", ""),
        "recent_messages": memory.get("recent_messages", []),
        "final_output": "",
        "error": None,
        "analyst_profile": {},
    }

    run_id = uuid.uuid4()
    config = RunnableConfig(run_id=run_id)

    logger.info(
        "graph_invoke_started",
        conversation_id=str(conversation_id),
        user_id=str(user_id),
        model=model,
        run_id=str(run_id),
    )

    async def _run_and_signal() -> dict:
        try:
            return await compiled_graph.ainvoke(initial_state, config=config)
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(_run_and_signal())

    try:
        # Drain events from the queue until the None sentinel signals completion.
        while True:
            event = await queue.get()
            if event is None:
                break
            event_type = event["type"]
            data = {k: v for k, v in event.items() if k != "type"}
            yield _sse(event_type, data)

        try:
            final_state = await task
        except Exception as e:
            logger.error(
                "graph_invoke_error",
                error=str(e),
                conversation_id=str(conversation_id),
            )
            yield _sse("error", {"message": str(e)})
            return

        # Ingest path: router classified as ingest, graph exited after router.
        if final_state.get("classification") == "ingest":
            yield _sse("done", {"message_id": None, "conversation_id": str(conversation_id)})
            return

        # Persist assistant message.
        assistant_message_id = None
        final_output = final_state.get("final_output") or ""
        try:
            async with AsyncSessionFactory() as db:
                msg = Message(
                    conversation_id=conversation_id,
                    role=MessageRole.assistant,
                    content=final_output,
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
        except Exception:
            logger.error(
                "assistant_message_save_failed",
                conversation_id=str(conversation_id),
                user_id=str(user_id),
            )
            yield _sse("error", {"message": "Failed to save assistant message"})
            return

        # Retrieve LangSmith trace URL (best-effort, never fails the request).
        await asyncio.sleep(1.0)
        trace_url: str | None = None
        try:
            from langsmith import Client as _LSClient  # noqa: PLC0415
            trace_url = _LSClient().read_run(str(run_id)).url
        except Exception as ls_err:
            logger.warning(
                "langsmith_trace_url_failed",
                run_id=str(run_id),
                error=str(ls_err),
            )

        if trace_url:
            try:
                async with AsyncSessionFactory() as db:
                    await db.execute(
                        update(Message)
                        .where(Message.id == assistant_message_id)
                        .values(agent_trace={"langsmith_url": trace_url})
                    )
                    await db.commit()
            except Exception:
                logger.warning(
                    "agent_trace_update_failed",
                    message_id=str(assistant_message_id),
                )

        # Rolling summary regeneration every 6 messages.
        try:
            async with AsyncSessionFactory() as db:
                count_result = await db.execute(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.conversation_id == conversation_id)
                )
                count = count_result.scalar_one()

                if count >= 6 and count % 6 == 0:
                    conv_result = await db.execute(
                        select(Conversation).where(Conversation.id == conversation_id)
                    )
                    conversation = conv_result.scalar_one_or_none()

                    if conversation:
                        msgs_result = await db.execute(
                            select(Message)
                            .where(Message.conversation_id == conversation_id)
                            .order_by(Message.created_at.desc())
                            .limit(6)
                        )
                        recent = list(reversed(msgs_result.scalars().all()))
                        recent_for_summary = [
                            {"role": m.role.value, "content": m.content} for m in recent
                        ]
                        openai_client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                        new_summary = await MemoryManager().regenerate_summary(
                            prior_summary=conversation.rolling_summary or "",
                            new_messages=recent_for_summary,
                            openai_client=openai_client,
                        )
                        await db.execute(
                            update(Conversation)
                            .where(Conversation.id == conversation_id)
                            .values(rolling_summary=new_summary)
                        )
                        await db.commit()
                        logger.debug(
                            "summary_regenerated",
                            conversation_id=str(conversation_id),
                            message_count=count,
                        )
        except Exception as sum_err:
            logger.warning("summary_regeneration_failed", error=str(sum_err))

        logger.info(
            "chat_stream_completed",
            conversation_id=str(conversation_id),
            user_id=str(user_id),
            assistant_message_id=str(assistant_message_id),
            token_count=len(final_output),
        )

        yield _sse("done", {
            "message_id": str(assistant_message_id),
            "conversation_id": str(conversation_id),
        })

    finally:
        reset_stream_queue(token)
        if not task.done():
            task.cancel()


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

        memory = await MemoryManager().load_memory(db, str(user.id), str(conversation_id))

    logger.info(
        "chat_stream_started",
        conversation_id=str(conversation_id),
        user_id=str(user.id),
    )

    return StreamingResponse(
        _stream_events(conversation_id, user.id, body.message, body.model, memory),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
