import uuid
from datetime import date

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import clerk_auth
from app.config import settings
from app.database import get_db
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.user import User
from app.schemas.document import (
    DocumentListResponse,
    DocumentRead,
    RetrieveDebugRequest,
    RetrieveDebugResponse,
)
from app.services.retrieval import retrieval_service
from app.tasks.ingestion import ingest_document

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile,
    doc_type: str = Form("other"),
    ticker: str | None = Form(None),
    filing_date: str | None = Form(None),
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    pdf_bytes = await file.read()

    if len(pdf_bytes) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="file too large")

    if pdf_bytes[:4] != b"%PDF":
        raise HTTPException(status_code=422, detail="file must be a PDF")

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
        filename=file.filename or "upload.pdf",
        doc_type=validated_doc_type,
        ticker=ticker,
        filing_date=parsed_date,
        status=DocumentStatus.pending,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    file_path = f"/tmp/{doc.id}.pdf"
    with open(file_path, "wb") as f:
        f.write(pdf_bytes)

    ingest_document.delay(str(doc.id), file_path)

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


@router.post("/retrieve-debug", response_model=RetrieveDebugResponse)
async def retrieve_debug(
    body: RetrieveDebugRequest,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    results = await retrieval_service.retrieve(db, user.id, body.query, body.top_k)
    return RetrieveDebugResponse(results=results)
