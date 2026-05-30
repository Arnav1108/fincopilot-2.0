import uuid
from datetime import datetime, timezone

import structlog
import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.auth import clerk_auth
from app.database import get_db
from app.models.conversation import Conversation, Message
from app.models.document import Document, DocumentChunk
from app.models.user import User
from app.schemas.conversation import ConversationRead, ConversationUpdate, MessageRead
from app.schemas.document import ChunkRead, DocumentListResponse, DocumentRead

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=ConversationRead)
async def create_conversation(
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    conv = Conversation(user_id=user.id, title="New Conversation")
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    logger.info("conversation_created", conversation_id=str(conv.id), user_id=str(user.id))
    return ConversationRead.model_validate(conv)


@router.get("/", response_model=list[ConversationRead])
async def list_conversations(
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None))
        .order_by(Conversation.updated_at.desc())
    )
    result = await db.execute(stmt)
    return [ConversationRead.model_validate(c) for c in result.scalars().all()]


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
        )
        .values(title=body.title, updated_at=datetime.now(timezone.utc))
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        logger.warning("conversation_not_found", conversation_id=str(conversation_id), user_id=str(user.id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.commit()

    fetch = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = fetch.scalar_one()
    return ConversationRead.model_validate(conv)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
        )
        .values(deleted_at=datetime.now(timezone.utc))
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        logger.warning("conversation_not_found", conversation_id=str(conversation_id), user_id=str(user.id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.commit()
    logger.info("conversation_deleted", conversation_id=str(conversation_id), user_id=str(user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user.id,
        Conversation.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()
    if conv is None:
        logger.warning("conversation_not_found", conversation_id=str(conversation_id), user_id=str(user.id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return [MessageRead.model_validate(m) for m in msg_result.scalars().all()]


@router.get("/{conversation_id}/documents", response_model=DocumentListResponse)
async def list_conversation_documents(
    conversation_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
        )
    )
    if conv_result.scalar_one_or_none() is None:
        logger.warning("conversation_not_found", conversation_id=str(conversation_id), user_id=str(user.id))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    stmt = (
        select(Document)
        .where(Document.conversation_id == conversation_id, Document.user_id == user.id)
        .order_by(Document.created_at.asc())
    )
    result = await db.execute(stmt)
    docs = result.scalars().all()
    logger.info(
        "conversation_documents_fetched",
        conversation_id=str(conversation_id),
        user_id=str(user.id),
        count=len(docs),
    )
    return DocumentListResponse(documents=[DocumentRead.model_validate(d) for d in docs])


@router.get("/{conversation_id}/chunks/{chunk_id}", response_model=ChunkRead)
async def get_chunk(
    conversation_id: uuid.UUID,
    chunk_id: uuid.UUID,
    user: User = Depends(clerk_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(DocumentChunk).where(
        DocumentChunk.id == chunk_id,
        DocumentChunk.conversation_id == conversation_id,
        DocumentChunk.user_id == user.id,
    )
    result = await db.execute(stmt)
    chunk = result.scalar_one_or_none()
    if chunk is None:
        logger.warning(
            "chunk_not_found",
            chunk_id=str(chunk_id),
            conversation_id=str(conversation_id),
            user_id=str(user.id),
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    logger.debug(
        "chunk_fetched",
        chunk_id=str(chunk_id),
        conversation_id=str(conversation_id),
        user_id=str(user.id),
    )
    return ChunkRead(
        id=chunk.id,
        document_id=chunk.document_id,
        conversation_id=chunk.conversation_id,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        metadata=chunk.chunk_metadata,
        created_at=chunk.created_at,
    )
