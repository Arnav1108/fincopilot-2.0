import uuid
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.user import User

BASE = "/api/v1/documents"
VALID_PDF = b"%PDF-1.4 fake pdf content"


# ── POST /api/v1/documents/upload ────────────────────────────────────────────

class TestUploadDocument:
    async def test_upload_valid_pdf(
        self, client: AsyncClient, db: AsyncSession, test_user: User
    ):
        with patch("app.api.v1.documents.ingest_document") as mock_task:
            with patch("builtins.open", mock_open()):
                mock_task.delay.return_value = None
                r = await client.post(
                    f"{BASE}/upload",
                    files={"file": ("test.pdf", VALID_PDF, "application/pdf")},
                )
        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "pending"
        doc_id = uuid.UUID(data["document_id"])

        result = await db.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
        assert doc is not None
        assert doc.status == DocumentStatus.pending

    async def test_upload_non_pdf(self, client: AsyncClient):
        with patch("app.api.v1.documents.ingest_document"):
            r = await client.post(
                f"{BASE}/upload",
                files={"file": ("test.txt", b"not a pdf at all", "application/pdf")},
            )
        assert r.status_code == 422
        assert r.json()["detail"] == "file must be a PDF"

    async def test_upload_too_large(self, client: AsyncClient):
        with patch("app.api.v1.documents.ingest_document"):
            with patch.object(settings, "MAX_UPLOAD_BYTES", 10):
                r = await client.post(
                    f"{BASE}/upload",
                    files={"file": ("big.pdf", b"%PDF" + b"x" * 20, "application/pdf")},
                )
        assert r.status_code == 422
        assert r.json()["detail"] == "file too large"

    async def test_upload_no_auth(self, anon_client: AsyncClient):
        r = await anon_client.post(
            f"{BASE}/upload",
            files={"file": ("test.pdf", VALID_PDF, "application/pdf")},
        )
        assert r.status_code == 401


# ── GET /api/v1/documents/ ────────────────────────────────────────────────────

class TestListDocuments:
    async def test_list_empty(self, client: AsyncClient):
        r = await client.get(f"{BASE}/")
        assert r.status_code == 200
        assert r.json()["documents"] == []

    async def test_list_own_documents(
        self, client: AsyncClient, db: AsyncSession, test_user: User
    ):
        doc1 = Document(
            user_id=test_user.id,
            filename="a.pdf",
            doc_type=DocumentType.other,
            status=DocumentStatus.pending,
        )
        doc2 = Document(
            user_id=test_user.id,
            filename="b.pdf",
            doc_type=DocumentType.filing_10k,
            status=DocumentStatus.pending,
        )
        db.add_all([doc1, doc2])
        await db.commit()

        r = await client.get(f"{BASE}/")
        assert r.status_code == 200
        ids = [d["id"] for d in r.json()["documents"]]
        assert str(doc1.id) in ids
        assert str(doc2.id) in ids

    async def test_list_isolation(
        self, client: AsyncClient, db: AsyncSession, test_user_2: User
    ):
        doc = Document(
            user_id=test_user_2.id,
            filename="other.pdf",
            doc_type=DocumentType.other,
            status=DocumentStatus.pending,
        )
        db.add(doc)
        await db.commit()

        r = await client.get(f"{BASE}/")
        assert r.status_code == 200
        assert r.json()["documents"] == []


# ── GET /api/v1/documents/{id} ────────────────────────────────────────────────

class TestGetDocument:
    async def test_get_own_document(
        self, client: AsyncClient, db: AsyncSession, test_user: User
    ):
        doc = Document(
            user_id=test_user.id,
            filename="mine.pdf",
            doc_type=DocumentType.other,
            status=DocumentStatus.pending,
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)

        r = await client.get(f"{BASE}/{doc.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == str(doc.id)
        assert data["filename"] == "mine.pdf"

    async def test_get_other_user_document(
        self, client: AsyncClient, db: AsyncSession, test_user_2: User
    ):
        doc = Document(
            user_id=test_user_2.id,
            filename="notmine.pdf",
            doc_type=DocumentType.other,
            status=DocumentStatus.pending,
        )
        db.add(doc)
        await db.commit()

        r = await client.get(f"{BASE}/{doc.id}")
        assert r.status_code == 404

    async def test_get_nonexistent(self, client: AsyncClient):
        r = await client.get(f"{BASE}/{uuid.uuid4()}")
        assert r.status_code == 404


# ── POST /api/v1/documents/retrieve-debug ────────────────────────────────────

class TestRetrieveDebug:
    async def test_retrieve_blank_query(self, client: AsyncClient):
        r = await client.post(
            f"{BASE}/retrieve-debug", json={"query": "", "top_k": 5}
        )
        assert r.status_code == 422

    async def test_retrieve_top_k_too_large(self, client: AsyncClient):
        r = await client.post(
            f"{BASE}/retrieve-debug", json={"query": "revenue", "top_k": 21}
        )
        assert r.status_code == 422

    async def test_retrieve_no_chunks(self, client: AsyncClient):
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.0] * 1535 + [1.0])]
        mock_instance = MagicMock()
        mock_instance.embeddings.create = AsyncMock(return_value=mock_response)

        with patch("app.services.retrieval.openai.AsyncOpenAI", return_value=mock_instance):
            r = await client.post(
                f"{BASE}/retrieve-debug",
                json={"query": "total revenue", "top_k": 5},
            )
        assert r.status_code == 200
        assert r.json()["results"] == []

    async def test_retrieve_no_auth(self, anon_client: AsyncClient):
        r = await anon_client.post(
            f"{BASE}/retrieve-debug", json={"query": "revenue", "top_k": 5}
        )
        assert r.status_code == 401
