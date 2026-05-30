from __future__ import annotations

import asyncio
import uuid

import openai
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import DocumentChunk
from app.schemas.document import ChunkResult

logger = structlog.get_logger(__name__)

_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_RERANK_CANDIDATE_MULTIPLIER = 3

try:
    from sentence_transformers import CrossEncoder as _CrossEncoderClass  # type: ignore[import-untyped]

    _cross_encoder = _CrossEncoderClass(_CROSS_ENCODER_MODEL)
except Exception as _ce_load_err:
    _cross_encoder = None
    logger.warning("reranker_load_failed", model=_CROSS_ENCODER_MODEL, error=str(_ce_load_err))


def _rerank_sync(chunks: list[ChunkResult], query: str, top_k: int) -> list[ChunkResult]:
    """Score *chunks* against *query* with the cross-encoder and return top_k sorted results.

    Raises on any failure so the caller can catch and fall back.
    """
    pairs = [(query, c.content) for c in chunks]
    scores = _cross_encoder.predict(pairs)  # type: ignore[union-attr]
    ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
    return [c for _, c in ranked[:top_k]]


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
        When the cross-encoder is available, fetches top_k * 3 candidates from pgvector
        then re-scores with the cross-encoder before trimming to top_k.
        Falls back to cosine-ranked order if the cross-encoder fails.
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

        # Fetch extra candidates when a reranker is ready; otherwise just top_k.
        candidate_k = top_k * _RERANK_CANDIDATE_MULTIPLIER if _cross_encoder is not None else top_k

        distance_expr = DocumentChunk.embedding.cosine_distance(query_vec).label("distance")

        stmt = (
            select(DocumentChunk, distance_expr)
            .where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.conversation_id == conversation_id,
                DocumentChunk.embedding.is_not(None),
            )
            .order_by(distance_expr)
            .limit(candidate_k)
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

        candidates = [
            ChunkResult(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                similarity_score=float(1.0 - distance),
                content=chunk.content,
                metadata=chunk.chunk_metadata,
            )
            for chunk, distance in rows
        ]

        results = await self._rerank(candidates, query, top_k)

        logger.debug(
            "retrieval_complete",
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            results_count=len(results),
            top_score=results[0].similarity_score if results else 0.0,
        )
        return results

    async def _rerank(
        self, candidates: list[ChunkResult], query: str, top_k: int
    ) -> list[ChunkResult]:
        """Rerank *candidates* with the cross-encoder; fall back to cosine order on failure."""
        if not candidates or _cross_encoder is None:
            return candidates[:top_k]

        try:
            reranked = await asyncio.to_thread(_rerank_sync, candidates, query, top_k)
            logger.debug(
                "reranker_applied",
                query_preview=query[:50],
                input_count=len(candidates),
                output_count=len(reranked),
            )
            return reranked
        except Exception as exc:
            logger.warning("reranker_failed", error=str(exc))
            return candidates[:top_k]


retrieval_service = RetrievalService()
