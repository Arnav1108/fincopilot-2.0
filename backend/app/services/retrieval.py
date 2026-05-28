from __future__ import annotations

import uuid

import openai
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import DocumentChunk
from app.schemas.document import ChunkResult

logger = structlog.get_logger(__name__)


class RetrievalService:
    async def retrieve(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        query: str,
        top_k: int = 5,
    ) -> list[ChunkResult]:
        """Return the top-k chunks for *query* scoped to a single conversation.

        Similarity scores are cosine similarity in [0.0, 1.0] (= 1 - cosine_distance).
        Delegates ranking and limiting to pgvector's HNSW index via the <=> operator.
        """
        logger.debug(
            "retrieval_called",
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            query_length=len(query),
            top_k=top_k,
        )

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=[query],
        )
        query_vec: list[float] = response.data[0].embedding

        distance_expr = DocumentChunk.embedding.cosine_distance(query_vec).label("distance")

        stmt = (
            select(DocumentChunk, distance_expr)
            .where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.conversation_id == conversation_id,
                DocumentChunk.embedding.is_not(None),
            )
            .order_by(distance_expr)
            .limit(top_k)
        )
        result = await db.execute(stmt)
        rows = result.all()

        if not rows:
            logger.debug(
                "retrieval_complete",
                user_id=str(user_id),
                conversation_id=str(conversation_id),
                results_count=0,
                top_score=0.0,
            )
            return []

        results = [
            ChunkResult(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                similarity_score=float(1.0 - distance),
                content=chunk.content,
                metadata=chunk.chunk_metadata,
            )
            for chunk, distance in rows
        ]

        logger.debug(
            "retrieval_complete",
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            results_count=len(results),
            top_score=results[0].similarity_score if results else 0.0,
        )
        return results


retrieval_service = RetrievalService()
