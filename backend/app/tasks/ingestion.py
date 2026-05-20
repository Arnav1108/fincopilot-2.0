import os
import time
import uuid
from datetime import datetime

import openai
import pypdf
import structlog
import tiktoken
from celery.exceptions import Retry
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.celery_app import celery_app
from app.config import settings
from app.models.document import Document, DocumentChunk, DocumentStatus

_SYNC_DATABASE_URL = settings.DATABASE_URL.replace(
    "postgresql+asyncpg://", "postgresql+psycopg2://"
)
_engine = create_engine(_SYNC_DATABASE_URL, pool_pre_ping=True)
_SessionLocal = sessionmaker(bind=_engine)


def _build_chunks(full_text: str, pages: list[tuple[int, str]]) -> list[dict]:
    enc = tiktoken.get_encoding("cl100k_base")
    all_tokens = enc.encode(full_text)

    # Cumulative char offset at each token boundary
    token_char_offsets = [0]
    running = 0
    for token_id in all_tokens:
        token_bytes = enc.decode_single_token_bytes(token_id)
        try:
            char_len = len(token_bytes.decode("utf-8"))
        except UnicodeDecodeError:
            char_len = len(token_bytes)
        running += char_len
        token_char_offsets.append(running)

    # Character range per page in the concatenated full_text ("\n"-joined)
    page_boundaries: list[tuple[int, int, int]] = []
    cumulative = 0
    for page_num, page_text in pages:
        start = cumulative
        end = cumulative + len(page_text)
        page_boundaries.append((start, end, page_num))
        cumulative = end + 1  # +1 for the "\n" separator between pages

    MAX_TOKENS = 800
    OVERLAP = 100
    chunks: list[dict] = []
    start = 0
    total = len(all_tokens)

    while start < total:
        end = min(start + MAX_TOKENS, total)
        chunk_text = enc.decode(all_tokens[start:end])
        chunk_start_char = token_char_offsets[start]
        chunk_end_char = token_char_offsets[end]

        page_nums = sorted(
            {
                pn
                for (ps, pe, pn) in page_boundaries
                if ps < chunk_end_char and pe > chunk_start_char
            }
        )

        chunks.append(
            {
                "chunk_index": len(chunks),
                "content": chunk_text,
                "page_numbers": page_nums,
            }
        )

        if end == total:
            break
        start = end - OVERLAP

    return chunks


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingestion.ingest_document")
def ingest_document(self, document_id: str, file_path: str) -> None:
    log = structlog.get_logger(__name__).bind(document_id=document_id)
    log.info("task_started")

    start_time = time.monotonic()
    db = _SessionLocal()

    try:
        # 1. Fetch document and set processing
        doc = db.get(Document, uuid.UUID(document_id))
        doc.status = DocumentStatus.processing
        doc.updated_at = datetime.utcnow()
        db.commit()

        # 2. Extract text page by page, keeping (page_index, text) tuples
        reader = pypdf.PdfReader(file_path)
        pages: list[tuple[int, str]] = [
            (i, page.extract_text() or "")
            for i, page in enumerate(reader.pages)
        ]
        full_text = "\n".join(text for _, text in pages)
        log.info(
            "extraction_complete",
            page_count=len(pages),
            char_count=len(full_text),
        )

        # 3. Guard: not enough extractable text
        if len(full_text.strip()) < 50:
            doc.status = DocumentStatus.failed
            doc.error_message = "no extractable text"
            doc.updated_at = datetime.utcnow()
            db.commit()
            return

        # 4. Chunk with sliding window (800 tokens, 100 overlap)
        chunk_dicts = _build_chunks(full_text, pages)
        log.info("chunking_complete", chunk_count=len(chunk_dicts))

        # 5. Embed all chunks in a single API call
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        try:
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=[c["content"] for c in chunk_dicts],
            )
        except (openai.APIError, openai.RateLimitError) as exc:
            db.rollback()
            if self.request.retries >= self.max_retries:
                # All retries exhausted: persist failure and stop
                doc = db.get(Document, uuid.UUID(document_id))
                doc.status = DocumentStatus.failed
                doc.error_message = str(exc)
                doc.updated_at = datetime.utcnow()
                db.commit()
                log.error("task_failed", error=str(exc))
                return
            log.warning(
                "task_failed_retrying",
                error=str(exc),
                retry_number=self.request.retries,
            )
            raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))

        log.info("embedding_complete")

        # 6. Build DocumentChunk objects (embeddings indexed positionally)
        chunks = [
            DocumentChunk(
                document_id=doc.id,
                user_id=doc.user_id,
                chunk_index=c["chunk_index"],
                content=c["content"],
                embedding=response.data[i].embedding,
                chunk_metadata={
                    "page_numbers": c["page_numbers"],
                    "chunk_index": c["chunk_index"],
                    "source_filename": doc.filename,
                },
            )
            for i, c in enumerate(chunk_dicts)
        ]

        # 7. Single bulk insert
        db.add_all(chunks)
        db.flush()

        # 8. Mark document ready
        doc.chunk_count = len(chunks)
        doc.status = DocumentStatus.ready
        doc.updated_at = datetime.utcnow()
        db.commit()

        elapsed = time.monotonic() - start_time
        log.info(
            "task_succeeded",
            chunk_count=len(chunks),
            elapsed_seconds=round(elapsed, 2),
        )

    except Retry:
        raise

    except Exception as exc:
        db.rollback()
        log.error("task_failed", error=str(exc))
        try:
            doc = db.get(Document, uuid.UUID(document_id))
            if doc is not None:
                doc.status = DocumentStatus.failed
                doc.error_message = str(exc)
                doc.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass

    finally:
        db.close()
        if os.path.exists(file_path):
            os.remove(file_path)
