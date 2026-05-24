import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.document import ChunkResult
from app.schemas.tools.document_retrieval import DocumentRetrievalInput
from app.tools.document_retrieval import DocumentRetrievalTool

_TOOL = DocumentRetrievalTool()
_USER_ID = uuid.uuid4()
_CONV_ID = uuid.uuid4()


def _make_chunk(ticker: str | None = None, doc_type: str | None = None) -> ChunkResult:
    meta: dict = {}
    if ticker:
        meta["ticker"] = ticker
    if doc_type:
        meta["doc_type"] = doc_type
    return ChunkResult(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        similarity_score=0.9,
        content="sample content",
        metadata=meta,
    )


def _patch_retrieval(chunks: list[ChunkResult]):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    retrieve_mock = AsyncMock(return_value=chunks)

    return (
        patch("app.tools.document_retrieval.AsyncSessionFactory", mock_factory),
        patch("app.tools.document_retrieval.retrieval_service.retrieve", retrieve_mock),
        retrieve_mock,
    )


@pytest.mark.asyncio
async def test_no_filters_returns_top_k():
    chunks = [_make_chunk() for _ in range(5)]
    p_factory, p_retrieve, retrieve_mock = _patch_retrieval(chunks)
    with p_factory, p_retrieve:
        result = await _TOOL(DocumentRetrievalInput(query="test query", user_id=_USER_ID, conversation_id=_CONV_ID))
    assert len(result.chunks) == 5


@pytest.mark.asyncio
async def test_ticker_filter_removes_non_matching():
    chunks = [_make_chunk("AAPL") for _ in range(3)] + [_make_chunk("MSFT") for _ in range(7)]
    p_factory, p_retrieve, retrieve_mock = _patch_retrieval(chunks)
    with p_factory, p_retrieve:
        result = await _TOOL(
            DocumentRetrievalInput(query="earnings", user_id=_USER_ID, conversation_id=_CONV_ID, ticker="AAPL", top_k=5)
        )
    assert len(result.chunks) == 3
    assert all(c.metadata["ticker"] == "AAPL" for c in result.chunks)


@pytest.mark.asyncio
async def test_empty_result_is_not_error():
    p_factory, p_retrieve, _ = _patch_retrieval([])
    with p_factory, p_retrieve:
        result = await _TOOL(DocumentRetrievalInput(query="nothing here", user_id=_USER_ID, conversation_id=_CONV_ID))
    assert result.chunks == []


@pytest.mark.asyncio
async def test_user_id_always_scoped():
    specific_user = uuid.uuid4()
    p_factory, p_retrieve, retrieve_mock = _patch_retrieval([])
    with p_factory, p_retrieve:
        await _TOOL(DocumentRetrievalInput(query="test", user_id=specific_user, conversation_id=_CONV_ID))
    call_kwargs = retrieve_mock.call_args
    assert call_kwargs.kwargs["user_id"] == specific_user
