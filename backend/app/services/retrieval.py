from __future__ import annotations

import uuid

import numpy as np
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
        query: str,
        top_k: int = 5,
    ) -> list[ChunkResult]:
        logger.debug(
            "retrieval_called",
            user_id=str(user_id),
            query_length=len(query),
            top_k=top_k,
        )

        # Embed the query using the async client (called from async endpoint)
        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=[query],
        )
        query_vec = np.array(response.data[0].embedding, dtype=np.float32)
        q_norm = np.linalg.norm(query_vec)

        # Fetch all user chunks that have embeddings — always filtered by user_id
        stmt = select(DocumentChunk).where(
            DocumentChunk.user_id == user_id,
            DocumentChunk.embedding.is_not(None),
        )
        result = await db.execute(stmt)
        chunks = result.scalars().all()

        if not chunks:
            logger.debug(
                "retrieval_complete",
                user_id=str(user_id),
                results_count=0,
                top_score=0.0,
            )
            return []

        # Compute cosine similarity in-process with numpy
        scored: list[tuple[DocumentChunk, float]] = []
        for chunk in chunks:
            chunk_vec = np.array(chunk.embedding, dtype=np.float32)
            c_norm = np.linalg.norm(chunk_vec)
            if q_norm == 0.0 or c_norm == 0.0:
                score = 0.0
            else:
                score = float(np.dot(query_vec, chunk_vec) / (q_norm * c_norm))
            scored.append((chunk, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:top_k]

        results = [
            ChunkResult(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                score=score,
                content=chunk.content,
                metadata=chunk.chunk_metadata,
            )
            for chunk, score in top
        ]

        logger.debug(
            "retrieval_complete",
            user_id=str(user_id),
            results_count=len(results),
            top_score=results[0].score if results else 0.0,
        )
        return results


retrieval_service = RetrievalService()
