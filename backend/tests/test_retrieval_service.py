import random
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.conversation import Conversation
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.user import User
from app.services.retrieval import retrieval_service

# Independent session factory — same DB as conftest (fincopilot_test) but a
# separate engine so it never shares a connection with the session-scoped
# setup_test_db fixture, avoiding "Future attached to a different loop" /
# "another operation is in progress" errors.
_base, _ = settings.DATABASE_URL.rsplit("/", 1)
_TEST_DB_URL = f"{_base}/fincopilot_test"
_test_engine = create_async_engine(_TEST_DB_URL, echo=False, poolclass=NullPool)
_IsolatedSession = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


# ── Local fixture overrides ────────────────────────────────────────────────────

@pytest.fixture
async def db():
    async with _IsolatedSession() as session:
        yield session


@pytest.fixture
async def test_user(db: AsyncSession):
    user = User(clerk_user_id=f"test_{uuid.uuid4()}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    yield user
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()


@pytest.fixture
async def test_user_2(db: AsyncSession):
    user = User(clerk_user_id=f"test_{uuid.uuid4()}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    yield user
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def make_conversation(db: AsyncSession, user_id: uuid.UUID) -> Conversation:
    conv = Conversation(user_id=user_id)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def make_document(
    db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Document:
    doc = Document(
        user_id=user_id,
        conversation_id=conversation_id,
        filename="test.pdf",
        doc_type=DocumentType.other,
        status=DocumentStatus.ready,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def make_chunk(
    db: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    content: str,
    embedding: list[float],
    chunk_index: int = 0,
) -> DocumentChunk:
    chunk = DocumentChunk(
        document_id=document_id,
        user_id=user_id,
        conversation_id=conversation_id,
        chunk_index=chunk_index,
        content=content,
        embedding=embedding,
    )
    db.add(chunk)
    await db.commit()
    await db.refresh(chunk)
    return chunk


def _mock_openai(query_embedding: list[float]) -> MagicMock:
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=query_embedding)]
    mock_instance = MagicMock()
    mock_instance.embeddings.create = AsyncMock(return_value=mock_response)
    return mock_instance


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_no_chunks_returns_empty(db: AsyncSession, test_user: User):
    conv = await make_conversation(db, test_user.id)
    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(db, test_user.id, conv.id, "revenue", top_k=5)
    assert results == []


async def test_returns_top_k(db: AsyncSession, test_user: User):
    conv = await make_conversation(db, test_user.id)
    doc = await make_document(db, test_user.id, conv.id)
    for i in range(10):
        emb = [random.random() for _ in range(1536)]
        await make_chunk(db, doc.id, test_user.id, conv.id, f"chunk {i}", emb, chunk_index=i)

    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(db, test_user.id, conv.id, "revenue", top_k=3)
    assert len(results) == 3


async def test_sorted_by_score(db: AsyncSession, test_user: User):
    conv = await make_conversation(db, test_user.id)
    doc = await make_document(db, test_user.id, conv.id)
    # query: unit vector along last dim → chunk_a (identical) scores 1.0,
    # chunk_b and chunk_c (orthogonal) score 0.0
    query_emb = [0.0] * 1535 + [1.0]
    chunk_a = await make_chunk(db, doc.id, test_user.id, conv.id, "best",  [0.0] * 1535 + [1.0],    chunk_index=0)
    await make_chunk(db, doc.id, test_user.id, conv.id,           "mid",  [0.0] * 1534 + [1.0, 0.0], chunk_index=1)
    await make_chunk(db, doc.id, test_user.id, conv.id,           "last", [1.0] + [0.0] * 1535,      chunk_index=2)

    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(db, test_user.id, conv.id, "best", top_k=3)

    assert results[0].chunk_id == chunk_a.id


async def test_user_isolation(
    db: AsyncSession, test_user: User, test_user_2: User
):
    conv1 = await make_conversation(db, test_user.id)
    conv2 = await make_conversation(db, test_user_2.id)
    doc1 = await make_document(db, test_user.id, conv1.id)
    doc2 = await make_document(db, test_user_2.id, conv2.id)
    emb = [0.0] * 1535 + [1.0]
    chunk1 = await make_chunk(db, doc1.id, test_user.id,   conv1.id, "user1", emb, chunk_index=0)
    chunk2 = await make_chunk(db, doc2.id, test_user_2.id, conv2.id, "user2", emb, chunk_index=0)

    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(db, test_user.id, conv1.id, "query", top_k=10)

    result_ids = {r.chunk_id for r in results}
    assert chunk1.id in result_ids
    assert chunk2.id not in result_ids


async def test_score_identical_vectors(db: AsyncSession, test_user: User):
    conv = await make_conversation(db, test_user.id)
    doc = await make_document(db, test_user.id, conv.id)
    emb = [0.0] * 1535 + [1.0]
    await make_chunk(db, doc.id, test_user.id, conv.id, "identical", emb, chunk_index=0)

    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(emb[:])):
        results = await retrieval_service.retrieve(db, test_user.id, conv.id, "query", top_k=1)

    assert len(results) == 1
    assert abs(results[0].similarity_score - 1.0) < 1e-5


async def test_score_orthogonal_vectors(db: AsyncSession, test_user: User):
    conv = await make_conversation(db, test_user.id)
    doc = await make_document(db, test_user.id, conv.id)
    chunk_emb = [1.0] + [0.0] * 1535        # unit vector along dim 0
    query_emb  = [0.0, 1.0] + [0.0] * 1534  # unit vector along dim 1
    await make_chunk(db, doc.id, test_user.id, conv.id, "orthogonal", chunk_emb, chunk_index=0)

    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(db, test_user.id, conv.id, "query", top_k=1)

    assert len(results) == 1
    assert abs(results[0].similarity_score) < 1e-5


async def test_chunk_result_includes_document_filename(db: AsyncSession, test_user: User):
    """Every ChunkResult must carry document_filename from the parent Document row."""
    conv = await make_conversation(db, test_user.id)
    doc = await make_document(db, test_user.id, conv.id)
    emb = [0.0] * 1535 + [1.0]
    await make_chunk(db, doc.id, test_user.id, conv.id, "AI investment content", emb, chunk_index=0)

    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(db, test_user.id, conv.id, "AI", top_k=1)

    assert len(results) == 1
    assert results[0].document_filename == "test.pdf"
    assert results[0].document_type == DocumentType.other.value


async def test_chunk_result_multi_document_filenames(db: AsyncSession, test_user: User):
    """Chunks from two different documents must each carry their own filename."""
    conv = await make_conversation(db, test_user.id)

    doc_a = Document(
        user_id=test_user.id,
        conversation_id=conv.id,
        filename="Apple_2024_10K.pdf",
        doc_type=DocumentType.filing_10k,
        status=DocumentStatus.ready,
    )
    doc_b = Document(
        user_id=test_user.id,
        conversation_id=conv.id,
        filename="Apple_Q3_2024_earnings.pdf",
        doc_type=DocumentType.transcript,
        status=DocumentStatus.ready,
    )
    db.add(doc_a)
    db.add(doc_b)
    await db.commit()
    await db.refresh(doc_a)
    await db.refresh(doc_b)

    emb = [0.0] * 1535 + [1.0]
    await make_chunk(db, doc_a.id, test_user.id, conv.id, "10-K AI content", emb, chunk_index=0)
    await make_chunk(db, doc_b.id, test_user.id, conv.id, "earnings AI content", emb, chunk_index=0)

    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(db, test_user.id, conv.id, "AI", top_k=5)

    filenames = {r.document_filename for r in results}
    assert "Apple_2024_10K.pdf" in filenames
    assert "Apple_Q3_2024_earnings.pdf" in filenames


# ── doc_ids filter tests ───────────────────────────────────────────────────────

async def _make_two_doc_conversation(db: AsyncSession, user_id: uuid.UUID):
    """Create a conversation with two documents (A and B), one chunk each."""
    conv = await make_conversation(db, user_id)
    doc_a = Document(
        user_id=user_id,
        conversation_id=conv.id,
        filename="apple_q2_2024.pdf",
        doc_type=DocumentType.filing_10q,
        status=DocumentStatus.ready,
        ticker="AAPL",
    )
    doc_b = Document(
        user_id=user_id,
        conversation_id=conv.id,
        filename="apple_q3_2024.pdf",
        doc_type=DocumentType.filing_10q,
        status=DocumentStatus.ready,
        ticker="AAPL",
    )
    db.add(doc_a)
    db.add(doc_b)
    await db.commit()
    await db.refresh(doc_a)
    await db.refresh(doc_b)

    emb = [0.0] * 1535 + [1.0]
    await make_chunk(db, doc_a.id, user_id, conv.id, "Q2 revenue guidance content", emb, chunk_index=0)
    await make_chunk(db, doc_b.id, user_id, conv.id, "Q3 revenue guidance content", emb, chunk_index=0)
    return conv, doc_a, doc_b


async def test_doc_ids_filter_isolates_to_one_document(db: AsyncSession, test_user: User):
    """retrieve(doc_ids=[doc_a.id]) returns only chunks from doc_a."""
    conv, doc_a, doc_b = await _make_two_doc_conversation(db, test_user.id)
    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(
            db, test_user.id, conv.id, "revenue guidance", top_k=5, doc_ids=[doc_a.id]
        )

    assert len(results) == 1
    assert results[0].document_id == doc_a.id
    assert results[0].document_filename == "apple_q2_2024.pdf"


async def test_doc_ids_unknown_uuid_returns_empty(db: AsyncSession, test_user: User):
    """retrieve(doc_ids=[unknown_uuid]) returns [] without raising."""
    conv, _, _ = await _make_two_doc_conversation(db, test_user.id)
    unknown = uuid.uuid4()
    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(
            db, test_user.id, conv.id, "revenue guidance", top_k=5, doc_ids=[unknown]
        )

    assert results == []


async def test_doc_ids_none_returns_all_documents(db: AsyncSession, test_user: User):
    """retrieve(doc_ids=None) returns chunks from all documents (regression)."""
    conv, doc_a, doc_b = await _make_two_doc_conversation(db, test_user.id)
    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(
            db, test_user.id, conv.id, "revenue guidance", top_k=10, doc_ids=None
        )

    doc_ids_found = {r.document_id for r in results}
    assert doc_a.id in doc_ids_found
    assert doc_b.id in doc_ids_found


async def test_attribution_metadata_fields_present(db: AsyncSession, test_user: User):
    """ChunkResult.metadata must contain doc_filename, doc_ticker, doc_type, doc_filing_date."""
    conv, doc_a, _ = await _make_two_doc_conversation(db, test_user.id)
    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(
            db, test_user.id, conv.id, "revenue", top_k=5, doc_ids=[doc_a.id]
        )

    assert len(results) == 1
    meta = results[0].metadata
    assert meta is not None
    assert meta["doc_filename"] == "apple_q2_2024.pdf"
    assert meta["doc_ticker"] == "AAPL"
    assert meta["doc_type"] == "10-Q"
    assert meta["doc_filing_date"] is None  # not set in fixture


async def test_attribution_metadata_null_ticker(db: AsyncSession, test_user: User):
    """When Document.ticker is null, metadata['doc_ticker'] is None (not absent)."""
    conv = await make_conversation(db, test_user.id)
    doc = Document(
        user_id=test_user.id,
        conversation_id=conv.id,
        filename="generic_report.pdf",
        doc_type=DocumentType.other,
        status=DocumentStatus.ready,
        ticker=None,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    emb = [0.0] * 1535 + [1.0]
    await make_chunk(db, doc.id, test_user.id, conv.id, "some content", emb, chunk_index=0)

    query_emb = [0.0] * 1535 + [1.0]
    with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=_mock_openai(query_emb)):
        results = await retrieval_service.retrieve(
            db, test_user.id, conv.id, "content", top_k=1
        )

    assert len(results) == 1
    meta = results[0].metadata
    assert "doc_ticker" in meta
    assert meta["doc_ticker"] is None
