from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import openai
import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
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
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.user import User
from app.services.title_generator import generate_title
from app.tasks.ingestion import ingest_document

router = APIRouter()
logger = structlog.get_logger(__name__)

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"pdf", "docx", "csv", "txt", "html"})
_MAX_FILE_BYTES: int = 100 * 1024 * 1024   # 100 MB per file
_INGEST_POLL_INTERVAL: float = 0.5          # seconds between DB polls
_INGEST_TIMEOUT: float = 300.0              # seconds before giving up on ingestion
_CONFIRM_PATTERN = re.compile(r"^CONFIRM:(yes|no):([0-9a-f-]{36})$")
_CONFIRM_TTL = 120  # seconds — must match document_finder.py


def _redis_client() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _infer_file_type(filename: str) -> str | None:
    """Return the normalised extension for supported file types, or None."""
    if "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext if ext in _ALLOWED_EXTENSIONS else None


def _calculate_relevance_score(chunks: list[dict]) -> float | None:
    """
    Option B — vector-based: mean cosine similarity of retrieved chunks.

    Scores are already computed by the retrieval service (cosine similarity in
    [0.0, 1.0]).  No extra LLM call.  Returns None when chunks is empty.
    """
    if not chunks:
        return None
    scores = [float(c.get("score", 0.0)) for c in chunks]
    return round(sum(scores) / len(scores), 4)


async def _ingest_files(
    files: list[UploadFile],
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> list[uuid.UUID]:
    """
    Validate, persist, and queue every uploaded file for ingestion.

    Raises HTTPException on any validation failure so the endpoint can return a
    proper 4xx *before* the SSE stream is opened — callers see a normal error
    response, not a broken event stream.

    Returns the list of Document UUIDs submitted to the Celery queue.
    """
    document_ids: list[uuid.UUID] = []

    for file in files:
        filename = file.filename or ""
        file_type = _infer_file_type(filename)
        if file_type is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unsupported file type for '{filename}'. "
                    f"Allowed extensions: {sorted(_ALLOWED_EXTENSIONS)}"
                ),
            )

        data = await file.read()
        if len(data) > _MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"File '{filename}' is {len(data):,} bytes, "
                    f"which exceeds the 100 MB limit."
                ),
            )

        async with AsyncSessionFactory() as db:
            doc = Document(
                user_id=user_id,
                conversation_id=conversation_id,
                filename=filename or f"upload.{file_type}",
                doc_type=DocumentType.other,
                status=DocumentStatus.pending,
            )
            db.add(doc)
            await db.commit()
            await db.refresh(doc)

        file_path = f"/app/uploads/{doc.id}.{file_type}"
        with open(file_path, "wb") as fh:
            fh.write(data)

        ingest_document.delay(str(doc.id), file_path, file_type, str(conversation_id))
        document_ids.append(doc.id)

        logger.info(
            "file_queued_for_ingestion",
            document_id=str(doc.id),
            filename=filename,
            file_type=file_type,
            file_size_bytes=len(data),
            conversation_id=str(conversation_id),
        )

    return document_ids


# ── SSE event stream ──────────────────────────────────────────────────────────

async def _stream_events(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    message: str,
    model: str,
    memory: dict,
    document_ids: list[uuid.UUID],
    has_uploaded_documents: bool,
) -> AsyncGenerator[str, None]:
    # ── Phase 1: wait for file ingestion ─────────────────────────────────────
    if document_ids:
        pending: set[uuid.UUID] = set(document_ids)
        failed_files: list[str] = []
        deadline = time.monotonic() + _INGEST_TIMEOUT

        yield _sse("ingest_progress", {
            "total": len(document_ids),
            "pending": len(pending),
            "ready": 0,
            "failed": 0,
        })

        while pending:
            if time.monotonic() >= deadline:
                yield _sse("error", {
                    "code": "ingest_timeout",
                    "message": (
                        f"File ingestion did not complete within "
                        f"{int(_INGEST_TIMEOUT)} seconds. "
                        "The documents are still processing — try again shortly."
                    ),
                    "pending_ids": [str(d) for d in pending],
                })
                return

            await asyncio.sleep(_INGEST_POLL_INTERVAL)

            async with AsyncSessionFactory() as db:
                result = await db.execute(
                    select(Document).where(Document.id.in_(list(pending)))
                )
                docs = result.scalars().all()

            for doc in docs:
                if doc.status == DocumentStatus.ready:
                    pending.discard(doc.id)
                elif doc.status == DocumentStatus.failed:
                    pending.discard(doc.id)
                    failed_files.append(
                        f"'{doc.filename}': {doc.error_message or 'processing error'}"
                    )

            ready_count = len(document_ids) - len(pending) - len(failed_files)
            yield _sse("ingest_progress", {
                "total": len(document_ids),
                "pending": len(pending),
                "ready": ready_count,
                "failed": len(failed_files),
            })

        if failed_files:
            yield _sse("error", {
                "code": "ingest_failed",
                "message": "One or more files failed to ingest.",
                "failed_files": failed_files,
            })
            return

        yield _sse("ingest_complete", {"document_count": len(document_ids)})

    # ── Phase 2: invoke the agent graph ──────────────────────────────────────
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
        "rag_used": False,
        "relevance_score": None,
        "retry_count": 0,
        "conversation_summary": memory.get("conversation_summary", ""),
        "recent_messages": memory.get("recent_messages", []),
        "final_output": "",
        "error": None,
        "analyst_profile": {},
        "has_uploaded_documents": has_uploaded_documents,
        "portfolio_data": None,
        "chart_data": None,
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
        # Drain agent events until the None sentinel signals graph completion.
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

        # Fatal node error: surface to user and skip all persistence.
        if final_state.get("error") and not final_state.get("final_output"):
            yield _sse("error", {"message": final_state["error"]})
            return

        # Ingest path: router classified as ingest, graph exited after router.
        if final_state.get("classification") == "ingest":
            yield _sse("done", {"message_id": None, "conversation_id": str(conversation_id)})
            return

        # ── Phase 3: compute RAG metadata from final state ────────────────
        final_output: str = final_state.get("final_output") or ""
        chart_data: dict | None = final_state.get("chart_data")
        retrieved_chunks: list[dict] = final_state.get("retrieved_chunks") or []

        rag_used: bool = bool(final_state.get("rag_used", False))

        # Relevance score: mean cosine similarity of retrieved chunks.
        # Only meaningful when retrieval was actually used.
        relevance_score: float | None = (
            _calculate_relevance_score(retrieved_chunks) if rag_used else None
        )

        # Chunk IDs for provenance tracking.  The metadata key "chunk_id" is
        # populated by the retrieval service via ChunkResult; once _normalize_chunks
        # is updated to unwrap DocumentRetrievalOutput these will be non-empty.
        retrieved_chunk_ids: list[str] = [
            str(meta["chunk_id"])
            for c in retrieved_chunks
            if (meta := c.get("metadata") or {}) and "chunk_id" in meta
        ]

        # ── Phase 4: persist the assistant message ────────────────────────
        assistant_message_id: uuid.UUID | None = None
        try:
            async with AsyncSessionFactory() as db:
                msg = Message(
                    conversation_id=conversation_id,
                    role=MessageRole.assistant,
                    content=final_output,
                    rag_used=rag_used,
                    relevance_score=relevance_score,
                    retrieved_chunk_ids=retrieved_chunk_ids if retrieved_chunk_ids else None,
                    chart_data=chart_data,
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

        # ── Phase 5: LangSmith trace URL (best-effort, never fails) ──────
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

        # ── Phase 6: rolling summary every 6 messages ────────────────────
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
            rag_used=rag_used,
            relevance_score=relevance_score,
            retrieved_chunk_count=len(retrieved_chunks),
        )

        if chart_data:
            yield _sse("chart_data", chart_data)

        yield _sse("done", {
            "message_id": str(assistant_message_id),
            "conversation_id": str(conversation_id),
        })

    finally:
        reset_stream_queue(token)
        if not task.done():
            task.cancel()


# ── HIL confirmation handler ─────────────────────────────────────────────────

async def _handle_confirmation(
    answer: str,
    token: str,
    conversation_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    """
    Writes the user's yes/no answer to Redis so document_finder's polling loop
    can pick it up. Returns a minimal SSE stream (confirmed + done).
    """
    redis_conn = _redis_client()
    try:
        await redis_conn.set(f"confirm:{token}", answer, ex=_CONFIRM_TTL)
        logger.info(
            "confirmation_response_stored",
            token=token,
            answer=answer,
            conversation_id=str(conversation_id),
        )
    except Exception as exc:
        logger.error("confirmation_redis_write_failed", token=token, error=str(exc))
    finally:
        await redis_conn.aclose()

    yield _sse("confirmed", {"token": token, "answer": answer})
    yield _sse("done", {"message_id": None, "conversation_id": str(conversation_id)})


# ── Background title generation ───────────────────────────────────────────────

async def _set_auto_title(conversation_id: uuid.UUID, message: str) -> None:
    try:
        title = await generate_title(message)
        if not title:
            return
        async with AsyncSessionFactory() as db:
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(title=title, updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
        logger.info("auto_title_set", conversation_id=str(conversation_id), title=title)
    except Exception as exc:
        logger.error("auto_title_failed", conversation_id=str(conversation_id), error=str(exc))


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/{conversation_id}/stream")
async def stream_chat(
    conversation_id: uuid.UUID,
    message: str = Form(..., max_length=10_000),
    model: str = Form("gpt-4o"),
    files: list[UploadFile] | None = File(None),
    user: User = Depends(clerk_auth),
):
    """
    Stream an agent response over SSE.

    Request format: multipart/form-data
      - message   (str, required)
      - model     (str, optional, default "gpt-4o")
      - files     (UploadFile[], optional — pdf, docx, csv, txt, html; max 100 MB each)

    SSE event sequence (with files):
      ingest_progress  →  ...  →  ingest_complete
      node_update  →  tool_call  →  token  →  ...  →  done

    SSE event sequence (no files):
      node_update  →  tool_call  →  token  →  ...  →  done
    """
    if not message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="message must not be blank",
        )

    # HIL: confirmation reply — write to Redis and return immediately, no DB write.
    confirm_match = _CONFIRM_PATTERN.match(message.strip())
    if confirm_match:
        answer, token = confirm_match.group(1), confirm_match.group(2)
        # Verify the token is a known pending confirmation before accepting.
        redis_conn = _redis_client()
        try:
            pending = await redis_conn.exists(f"confirm:{token}:pending")
        finally:
            await redis_conn.aclose()
        if not pending:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown or expired confirmation token")
        return StreamingResponse(
            _handle_confirmation(answer, token, conversation_id),
            media_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 1. Validate conversation ownership and persist the user message.
    is_first_user_message = False
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

        user_msg = Message(
            conversation_id=conversation_id,
            role=MessageRole.user,
            content=message,
        )
        db.add(user_msg)
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

        user_count_result = await db.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.user,
            )
        )
        is_first_user_message = user_count_result.scalar_one() == 1

        memory = await MemoryManager().load_memory(db, str(user.id), str(conversation_id))

        doc_count_result = await db.execute(
            select(func.count(Document.id)).where(
                Document.conversation_id == conversation_id,
                Document.status == DocumentStatus.ready,
            )
        )
        has_uploaded_documents = (doc_count_result.scalar() or 0) > 0

    if is_first_user_message:
        asyncio.create_task(_set_auto_title(conversation_id, message))

    # 2. Validate + queue uploaded files.  Raises 422 immediately on bad input
    #    so the client gets a normal HTTP error before any SSE frames are sent.
    document_ids: list[uuid.UUID] = []
    if files:
        non_empty = [f for f in files if f.filename]
        if non_empty:
            document_ids = await _ingest_files(non_empty, user.id, conversation_id)

    # Newly uploaded files will be ready by the time Phase 2 (agent) starts —
    # Phase 1 polls until all document_ids reach DocumentStatus.ready before
    # the agent runs. So flag the conversation as having documents regardless of
    # the pre-ingestion count.
    if document_ids:
        has_uploaded_documents = True

    logger.info(
        "chat_stream_started",
        conversation_id=str(conversation_id),
        user_id=str(user.id),
        file_count=len(document_ids),
    )

    return StreamingResponse(
        _stream_events(
            conversation_id,
            user.id,
            message,
            model,
            memory,
            document_ids,
            has_uploaded_documents,
        ),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
