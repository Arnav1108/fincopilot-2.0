from __future__ import annotations

import structlog

from app.database import AsyncSessionFactory
from app.schemas.tools.document_retrieval import DocumentRetrievalInput, DocumentRetrievalOutput
from app.services.retrieval import retrieval_service
from app.tools.base import BaseTool

logger = structlog.get_logger(__name__)

_FILTER_MULTIPLIER = 4


class DocumentRetrievalTool(BaseTool[DocumentRetrievalInput, DocumentRetrievalOutput]):
    async def __call__(self, input: DocumentRetrievalInput) -> DocumentRetrievalOutput:  # noqa: A002
        logger.debug("tool_called", tool_name="document_retrieval", query_length=len(input.query))

        has_filters = any(
            f is not None for f in (input.ticker, input.doc_type, input.fiscal_year)
        )
        fetch_k = input.top_k * _FILTER_MULTIPLIER if has_filters else input.top_k

        async with AsyncSessionFactory() as db:
            chunks = await retrieval_service.retrieve(
                db=db,
                user_id=input.user_id,
                query=input.query,
                top_k=fetch_k,
            )

        if has_filters:
            pre_count = len(chunks)
            chunks = self._apply_filters(chunks, input)
            chunks = chunks[: input.top_k]
            logger.debug(
                "document_retrieval_filtered",
                pre_filter_count=pre_count,
                post_filter_count=len(chunks),
                filters_applied={
                    "ticker": input.ticker,
                    "doc_type": input.doc_type,
                    "fiscal_year": input.fiscal_year,
                },
            )

        out = DocumentRetrievalOutput(chunks=chunks)
        logger.debug("tool_succeeded", tool_name="document_retrieval", chunk_count=len(chunks))
        return out

    @staticmethod
    def _apply_filters(chunks, input: DocumentRetrievalInput):
        result = []
        for chunk in chunks:
            meta = chunk.metadata or {}
            if input.ticker and meta.get("ticker") != input.ticker:
                continue
            if input.doc_type and meta.get("doc_type") != input.doc_type:
                continue
            if input.fiscal_year and meta.get("fiscal_year") != input.fiscal_year:
                continue
            result.append(chunk)
        return result
