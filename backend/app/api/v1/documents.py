import asyncio
import json
import time
import uuid
from datetime import date
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import clerk_auth
from app.config import settings
from app.database import AsyncSessionFactory, get_db
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.user import User
from app.schemas.document import (
    DocumentListResponse,
    DocumentRead,
    RetrieveDebugRequest,
    RetrieveDebugResponse,
)
from app.services.redis_pubsub import get_async_redis, subscribe_to_channel
from app.services.retrieval import retrieval_service
from app.tasks.ingestion import ingest_document

router = APIRouter()
logger = structlog.get_logger(__name__)

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"pdf", "docx", "csv", "txt", "html"})


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile,
    conversation_id: uuid.UUID = Form(...),
    doc_type: str = Form("other"),
    ticker: str | None = Form(None),
    filing_date: str | None = Form(None),
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    file_type = ext if ext in _ALLOWED_EXTENSIONS else None
    if file_type is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type. Allowed extensions: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    file_bytes = await file.read()

    if len(file_bytes) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="file too large")

    parsed_date: date | None = None
    if filing_date is not None:
        try:
            parsed_date = date.fromisoformat(filing_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid filing_date format")

    try:
        validated_doc_type = DocumentType(doc_type)
    except ValueError:
        validated_doc_type = DocumentType.other

    doc = Document(
        user_id=user.id,
        conversation_id=conversation_id,
        filename=filename or f"upload.{file_type}",
        doc_type=validated_doc_type,
        ticker=ticker,
        filing_date=parsed_date,
        status=DocumentStatus.pending,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    file_path = f"/tmp/{doc.id}.{file_type}"
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    ingest_document.delay(str(doc.id), file_path, file_type, str(conversation_id))

    logger.info(
        "document_upload_received",
        document_id=str(doc.id),
        filename=doc.filename,
        user_id=str(user.id),
        file_size_bytes=len(pdf_bytes),
    )

    return JSONResponse(
        status_code=202,
        content={"document_id": str(doc.id), "status": "pending"},
    )


@router.get("/", response_model=DocumentListResponse)
async def list_documents(
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Document)
        .where(Document.user_id == user.id)
        .order_by(Document.created_at.desc())
    )
    result = await db.execute(stmt)
    docs = result.scalars().all()
    return DocumentListResponse(documents=[DocumentRead.model_validate(d) for d in docs])


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Document).where(
        Document.id == document_id,
        Document.user_id == user.id,
    )
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return DocumentRead.model_validate(doc)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _status_stream_events(
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    channel = f"document.{document_id}.status"
    start = time.monotonic()
    try:
        async with subscribe_to_channel(channel) as pubsub:
            # Subscribe BEFORE querying DB — prevents missing events published
            # between the ownership check and subscription (race condition fix).
            async with AsyncSessionFactory() as db:
                result = await db.execute(select(Document).where(Document.id == document_id))
                doc = result.scalar_one()

            logger.info(
                "sse_stream_opened",
                document_id=str(document_id),
                user_id=str(user_id),
                current_status=doc.status.value,
            )

            yield _sse("status", {
                "status": doc.status.value,
                "chunk_count": doc.chunk_count,
                "error_message": doc.error_message,
            })

            if doc.status in (DocumentStatus.ready, DocumentStatus.failed):
                logger.info(
                    "sse_stream_closed",
                    document_id=str(document_id),
                    reason="terminal_event",
                    final_status=doc.status.value,
                )
                return

            last_event = time.monotonic()
            while True:
                if time.monotonic() - start >= 300:
                    yield _sse("timeout", {"message": "stream closed after maximum duration"})
                    logger.info("sse_stream_closed", document_id=str(document_id), reason="timeout")
                    return

                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

                if msg and msg["type"] == "message":
                    data = json.loads(msg["data"])
                    logger.debug(
                        "sse_event_emitted",
                        document_id=str(document_id),
                        event_type="status",
                        status=data.get("status"),
                    )
                    yield _sse("status", data)
                    last_event = time.monotonic()
                    if data.get("status") in ("ready", "failed"):
                        logger.info(
                            "sse_stream_closed",
                            document_id=str(document_id),
                            reason="terminal_event",
                            final_status=data.get("status"),
                        )
                        return
                else:
                    if time.monotonic() - last_event >= 60:
                        yield _sse("ping", {})
                        last_event = time.monotonic()

    except asyncio.CancelledError:
        logger.info("sse_stream_closed", document_id=str(document_id), reason="client_disconnect")
        raise


@router.get("/{document_id}/status-stream")
async def stream_document_status(
    document_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Document).where(Document.id == document_id, Document.user_id == user.id)
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        r = get_async_redis()
        await r.ping()
        await r.aclose()
    except Exception:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service unavailable")
    return StreamingResponse(
        _status_stream_events(document_id, user.id),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/retrieve-debug", response_model=RetrieveDebugResponse)
async def retrieve_debug(
    body: RetrieveDebugRequest,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    results = await retrieval_service.retrieve(db, user.id, body.conversation_id, body.query, body.top_k)
    return RetrieveDebugResponse(results=results)
