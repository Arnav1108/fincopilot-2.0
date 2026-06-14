from __future__ import annotations

import asyncio
import uuid

import openai
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document, DocumentChunk
from app.schemas.document import ChunkResult

logger = structlog.get_logger(__name__)

_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_RERANK_CANDIDATE_MULTIPLIER = 3

_cross_encoder = None
_cross_encoder_load_failed = False


def _get_cross_encoder():
    """Lazily load and cache the cross-encoder model on first use."""
    global _cross_encoder, _cross_encoder_load_failed
    if _cross_encoder is None and not _cross_encoder_load_failed:
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]

            _cross_encoder = CrossEncoder(_CROSS_ENCODER_MODEL)
        except Exception as exc:
            _cross_encoder_load_failed = True
            logger.warning("reranker_load_failed", model=_CROSS_ENCODER_MODEL, error=str(exc))
    return _cross_encoder


def _rerank_sync(chunks: list[ChunkResult], query: str, top_k: int) -> list[ChunkResult]:
    """Score *chunks* against *query* with the cross-encoder and return top_k sorted results.

    Raises on any failure so the caller can catch and fall back.
    """
    cross_encoder = _get_cross_encoder()
    if cross_encoder is None:
        raise RuntimeError("reranker unavailable")
    pairs = [(query, c.content) for c in chunks]
    scores = cross_encoder.predict(pairs)
    ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
    return [c for _, c in ranked[:top_k]]


def _build_chunk_result(chunk: DocumentChunk, doc: Document, score: float) -> ChunkResult:
    return ChunkResult(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        similarity_score=score,
        content=chunk.content,
        metadata={
            **(chunk.chunk_metadata or {}),
            "doc_filename": doc.filename,
            "doc_ticker": doc.ticker,
            "doc_type": doc.doc_type.value if doc.doc_type else None,
            "doc_filing_date": doc.filing_date.isoformat() if doc.filing_date else None,
        },
        document_filename=doc.filename,
        document_type=doc.doc_type.value if doc.doc_type else None,
    )


class RetrievalService:
    async def retrieve(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        query: str,
        top_k: int = 5,
        doc_ids: list[uuid.UUID] | None = None,
    ) -> list[ChunkResult]:
        """Return the top-k chunks for *query* scoped to a single conversation.

        Combines pgvector cosine similarity with PostgreSQL FTS (hybrid search).
        When FTS returns results, merges both candidate sets and computes
        hybrid_score = alpha * cosine + (1 - alpha) * bm25_normalized.
        Falls back to pure vector when FTS returns no results or raises.
        Falls back to cosine-ranked order if the cross-encoder fails.

        When doc_ids is provided, restricts retrieval to chunks from those documents only.
        The Document table is always joined to populate attribution metadata on each chunk.
        """
        logger.debug(
            "retrieval_called",
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            query_length=len(query),
            top_k=top_k,
            doc_ids_filter_count=len(doc_ids) if doc_ids else 0,
        )

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=[query],
        )
        query_vec: list[float] = response.data[0].embedding

        # Fetch extra candidates so the reranker has a pool to work with.
        candidate_k = top_k * _RERANK_CANDIDATE_MULTIPLIER

        distance_expr = DocumentChunk.embedding.cosine_distance(query_vec).label("distance")

        conditions = [
            DocumentChunk.user_id == user_id,
            DocumentChunk.conversation_id == conversation_id,
            DocumentChunk.embedding.is_not(None),
        ]
        if doc_ids:
            conditions.append(DocumentChunk.document_id.in_(doc_ids))

        stmt = (
            select(DocumentChunk, Document, distance_expr)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(*conditions)
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
                hybrid_search_applied=False,
                fts_candidate_count=0,
            )
            return []

        # Build vector_map: chunk_id → ChunkResult (cosine similarity score)
        vector_map: dict[uuid.UUID, ChunkResult] = {
            chunk.id: _build_chunk_result(chunk, doc, float(1.0 - distance))
            for chunk, doc, distance in rows
        }

        # FTS query — exception caught here; empty result triggers pure-vector fallback
        fts_rows: list = []
        try:
            fts_rows = await self._fts_query(db, user_id, conversation_id, query, candidate_k, doc_ids)
        except Exception as exc:
            logger.warning("fts_query_failed", error=str(exc))

        if not fts_rows:
            candidates = list(vector_map.values())
            hybrid_search_applied = False
            fts_candidate_count = 0
        else:
            # Build fts_result_map: chunk_id → (ChunkResult, raw_fts_score)
            fts_result_map: dict[uuid.UUID, tuple[ChunkResult, float]] = {}
            for chunk, doc, fts_score in fts_rows:
                fts_result_map[chunk.id] = (_build_chunk_result(chunk, doc, 0.0), float(fts_score))

            # Min-max normalize FTS scores to [0, 1]
            raw_scores = [s for _, s in fts_result_map.values()]
            min_s = min(raw_scores)
            max_s = max(raw_scores)
            if max_s == min_s:
                bm25_norm: dict[uuid.UUID, float] = {cid: 1.0 for cid in fts_result_map}
            else:
                bm25_norm = {
                    cid: (s - min_s) / (max_s - min_s)
                    for cid, (_, s) in fts_result_map.items()
                }

            # Merge: union of both candidate sets; compute hybrid score for each
            alpha = settings.HYBRID_SEARCH_ALPHA
            merged: list[ChunkResult] = []
            for cid in set(vector_map) | set(fts_result_map):
                vec_score = vector_map[cid].similarity_score if cid in vector_map else 0.0
                bm25 = bm25_norm.get(cid, 0.0)
                hybrid_score = alpha * vec_score + (1.0 - alpha) * bm25
                base_cr = vector_map.get(cid) or fts_result_map[cid][0]
                merged.append(base_cr.model_copy(update={"similarity_score": hybrid_score}))

            candidates = sorted(merged, key=lambda c: c.similarity_score, reverse=True)[:candidate_k]
            hybrid_search_applied = True
            fts_candidate_count = len(fts_rows)

            logger.debug(
                "hybrid_search_applied",
                fts_candidate_count=fts_candidate_count,
                vector_candidate_count=len(rows),
                merged_candidate_count=len(candidates),
            )

        results = await self._rerank(candidates, query, top_k)

        logger.debug(
            "retrieval_complete",
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            results_count=len(results),
            top_score=results[0].similarity_score if results else 0.0,
            hybrid_search_applied=hybrid_search_applied,
            fts_candidate_count=fts_candidate_count,
        )
        return results

    async def _fts_query(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        query: str,
        candidate_k: int,
        doc_ids: list[uuid.UUID] | None = None,
    ) -> list:
        """Run a full-text search query against content_tsv and return (chunk, doc, score) rows."""
        tsquery_expr = func.websearch_to_tsquery("english", query)
        fts_score_expr = func.ts_rank_cd(DocumentChunk.content_tsv, tsquery_expr).label("fts_score")

        conditions = [
            DocumentChunk.user_id == user_id,
            DocumentChunk.conversation_id == conversation_id,
            DocumentChunk.embedding.is_not(None),
            DocumentChunk.content_tsv.op("@@")(tsquery_expr),
        ]
        if doc_ids:
            conditions.append(DocumentChunk.document_id.in_(doc_ids))

        stmt = (
            select(DocumentChunk, Document, fts_score_expr)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(*conditions)
            .order_by(fts_score_expr.desc())
            .limit(candidate_k)
        )
        result = await db.execute(stmt)
        return result.all()

    async def _rerank(
        self, candidates: list[ChunkResult], query: str, top_k: int
    ) -> list[ChunkResult]:
        """Rerank *candidates* with the cross-encoder; fall back to cosine order on failure."""
        if not candidates:
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
