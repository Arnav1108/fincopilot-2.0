import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import tiktoken

from app.models.document import DocumentStatus
from app.tasks.ingestion import _build_chunks, ingest_document


def _make_text(n_tokens: int) -> str:
    """Return a string that encodes to exactly n_tokens with cl100k_base."""
    enc = tiktoken.get_encoding("cl100k_base")
    base = "The quick brown fox jumps over the lazy dog. "
    base_tokens = enc.encode(base)
    repeats = (n_tokens // len(base_tokens)) + 1
    full_tokens = enc.encode(base * repeats)[:n_tokens]
    return enc.decode(full_tokens)


# ── Chunking unit tests ───────────────────────────────────────────────────────

class TestChunking:
    def test_chunk_count(self):
        text = _make_text(2000)
        # 0→799, 700→1499, 1400→1999: three windows
        assert len(_build_chunks(text, [(0, text)])) == 3

    def test_chunk_max_tokens(self):
        enc = tiktoken.get_encoding("cl100k_base")
        text = _make_text(3000)
        for chunk in _build_chunks(text, [(0, text)]):
            assert len(enc.encode(chunk["content"])) <= 800

    def test_chunk_overlap(self):
        enc = tiktoken.get_encoding("cl100k_base")
        text = _make_text(2000)
        chunks = _build_chunks(text, [(0, text)])
        assert len(chunks) >= 2
        # Chunk 1 starts at token 700 (= 800 − 100 overlap)
        all_tokens = enc.encode(text)
        expected = enc.decode(all_tokens[700:1500])
        assert chunks[1]["content"] == expected

    def test_short_text_rejected(self):
        # Empty text → 0 tokens → while loop never runs → []
        assert _build_chunks("", [(0, "")]) == []


# ── Ingestion task unit tests ─────────────────────────────────────────────────

class TestIngestDocument:
    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _mock_db(doc_id: str) -> tuple[MagicMock, MagicMock]:
        mock_doc = MagicMock()
        mock_doc.id = uuid.UUID(doc_id)
        mock_doc.user_id = uuid.uuid4()
        mock_doc.filename = "test.pdf"
        mock_db = MagicMock()
        mock_db.get.return_value = mock_doc
        return mock_db, mock_doc

    @staticmethod
    def _mock_reader(page_text: str, n_pages: int = 3) -> MagicMock:
        mock_page = MagicMock()
        mock_page.extract_text.return_value = page_text
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page] * n_pages
        return mock_reader

    @staticmethod
    def _mock_openai(embedding: list[float]) -> MagicMock:
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=embedding)] * 20
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response
        return MagicMock(return_value=mock_client)

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_happy_path(self):
        doc_id = str(uuid.uuid4())
        page_text = "The quick brown fox jumps over the lazy dog. " * 7
        mock_db, mock_doc = self._mock_db(doc_id)

        with patch("app.tasks.ingestion._SessionLocal", return_value=mock_db), \
             patch("app.tasks.ingestion.pypdf.PdfReader", return_value=self._mock_reader(page_text)), \
             patch("app.tasks.ingestion.openai.OpenAI", self._mock_openai([0.1] * 1536)):
            ingest_document.apply(args=[doc_id, "/nonexistent/fake.pdf"])

        mock_db.add_all.assert_called_once()
        assert mock_doc.status == DocumentStatus.ready
        assert mock_doc.chunk_count > 0

    def test_no_extractable_text(self):
        doc_id = str(uuid.uuid4())
        mock_db, mock_doc = self._mock_db(doc_id)

        with patch("app.tasks.ingestion._SessionLocal", return_value=mock_db), \
             patch("app.tasks.ingestion.pypdf.PdfReader", return_value=self._mock_reader("")):
            ingest_document.apply(args=[doc_id, "/nonexistent/fake.pdf"])

        mock_db.add_all.assert_not_called()
        assert mock_doc.status == DocumentStatus.failed

    def test_tmp_file_deleted(self):
        doc_id = str(uuid.uuid4())
        page_text = "The quick brown fox jumps over the lazy dog. " * 7
        mock_db, _ = self._mock_db(doc_id)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(b"%PDF-1.4 test")
            file_path = f.name

        with patch("app.tasks.ingestion._SessionLocal", return_value=mock_db), \
             patch("app.tasks.ingestion.pypdf.PdfReader", return_value=self._mock_reader(page_text)), \
             patch("app.tasks.ingestion.openai.OpenAI", self._mock_openai([0.1] * 1536)):
            ingest_document.apply(args=[doc_id, file_path])

        assert not os.path.exists(file_path)

    def test_tmp_file_deleted_on_failure(self):
        doc_id = str(uuid.uuid4())
        mock_db, _ = self._mock_db(doc_id)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(b"%PDF-1.4 test")
            file_path = f.name

        with patch("app.tasks.ingestion._SessionLocal", return_value=mock_db), \
             patch("app.tasks.ingestion.pypdf.PdfReader", side_effect=Exception("corrupt pdf")):
            ingest_document.apply(args=[doc_id, file_path])

        assert not os.path.exists(file_path)
