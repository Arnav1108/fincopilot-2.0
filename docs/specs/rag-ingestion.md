# Spec: RAG Ingestion

## Goal
Allow authenticated end users to upload their own financial PDFs (10-Ks, annual reports, research notes) so they can query those documents in chat — with strict per-user data isolation at every layer.

## Background
The `documents` and `document_chunks` tables were created in migration `0001_initial_schema`. The `Document`, `DocumentChunk`, `DocumentType`, and `DocumentStatus` SQLAlchemy models live in `backend/app/models/document.py`. Celery is configured in `backend/app/celery_app.py` but has no tasks registered yet. There are no upload endpoints, no ingestion tasks, and no retrieval service. The `documents` table is missing a `chunk_count` column (added in migration `0004`).

## Scope

### In scope
- `POST /api/v1/documents/upload` — multipart form endpoint that accepts a PDF and returns 202 with `document_id` and `status=pending`
- `GET /api/v1/documents` — list the authenticated user's documents with id, filename, status, chunk_count, created_at
- `GET /api/v1/documents/{document_id}` — fetch a single document (scoped to the authenticated user)
- Alembic migration `0004` adding `chunk_count INTEGER NULL` to `documents`
- Celery task `ingest_document` that: extracts text from PDF, chunks it, calls OpenAI embeddings API, writes `DocumentChunk` rows, updates document status to `ready`/`failed`
- `RetrievalService` — a backend-only class with a single method `retrieve(user_id, query, top_k=5)` that embeds the query and returns the top-k scored chunks for that user using cosine similarity computed in-process with numpy
- `POST /api/v1/documents/retrieve-debug` — a debug endpoint to manually test retrieval end to end; returns top-5 chunks with similarity scores
- Per-user isolation enforced at every pgvector/SQL query via `WHERE user_id = :user_id`

### Out of scope
- URL/HTML ingestion
- Re-ingestion or updating an existing document
- Document deletion endpoint
- Redis pub/sub or SSE for real-time ingestion status (separate feature)
- Frontend upload UI (separate feature)
- SEC EDGAR filing fetch (separate feature)
- Wiring retrieval into the chat stream (separate feature)
- OCR for scanned documents
- Re-ranking with a separate API (cosine similarity only)
- Multi-tenant admin access across users

## User flow

### Happy path
1. User POSTs `multipart/form-data` to `POST /api/v1/documents/upload` with the PDF binary, optional `doc_type`, `ticker`, and `filing_date` fields.
2. API validates: file is present, content-type is `application/pdf`, size ≤ 50 MB.
3. API writes the raw PDF bytes to `/tmp/{document_id}.pdf` on disk, creates a `Document` row with `status=pending`, and enqueues the `ingest_document` Celery task with `document_id` (UUID string) and `file_path` (string). Do not pass PDF bytes or base64 in the task payload.
4. API returns `202 Accepted` with `{ "document_id": "<uuid>", "status": "pending" }`.
5. Celery worker picks up the task, sets `status=processing`.
6. Worker extracts text from the PDF using `pypdf`, splits into chunks (≤ 800 tokens each, 100-token overlap), calls `openai.embeddings.create` for each chunk using model `text-embedding-3-small`.
7. Worker bulk-inserts `DocumentChunk` rows (content, embedding, chunk_index, metadata JSONB).
8. Worker sets `document.status=ready` and `document.chunk_count=<n>`.
9. User can now call `POST /api/v1/documents/retrieve-debug` with a question and receives top-5 chunks with similarity scores.

### Error states
- File missing or wrong content-type → 422 with field-level error.
- File > 50 MB → 422 `"file too large"`.
- PDF has no extractable text (0 bytes after extraction) → task sets `status=failed`, `error_message="no extractable text"`.
- OpenAI API call fails → task retries up to 3 times with exponential backoff; after max retries sets `status=failed`, `error_message` contains the OpenAI error message.
- Any unexpected exception in the task → `status=failed`, `error_message` contains the exception string.
- `GET /api/v1/documents/{document_id}` for a document belonging to another user → 404 (not 403, to avoid leaking existence).
- `POST /api/v1/documents/retrieve-debug` while document is still `pending` or `processing` → returns empty results list (does not error).

## Detailed requirements

1. The upload endpoint MUST accept `multipart/form-data` with a field named `file` (PDF binary) and optional fields `doc_type` (one of the `DocumentType` enum values; default `other`), `ticker` (string ≤ 20 chars), `filing_date` (ISO 8601 date string).
2. The upload endpoint MUST validate the file is a PDF by reading the first 4 bytes and checking they equal `%PDF`. Return 422 with `detail: "file must be a PDF"` if the check fails.
3. The upload endpoint MUST reject files whose size exceeds 50,000,000 bytes (50 MB) before reading the entire payload.
4. The upload endpoint MUST return `202 Accepted` immediately; ingestion MUST NOT run on the API thread.
5. The `Document` row MUST be created with `user_id` set to the authenticated Clerk user's internal `users.id` (UUID), not the Clerk user ID string.
6. PDF text extraction MUST use `pypdf.PdfReader`; if the extracted text across all pages totals fewer than 50 characters the document MUST be marked `failed` with an appropriate error message.
7. Chunking MUST split text using a sliding window of 800 tokens maximum per chunk with 100-token overlap, measured in tokens using `tiktoken` with the `cl100k_base` encoding.
8. All chunk texts MUST be embedded in a single `openai.embeddings.create(model="text-embedding-3-small", input=[list_of_chunk_texts])` call. The response embeddings are indexed positionally to match the chunk list. Do not make one embedding call per chunk. Embeddings MUST be stored as `Vector(1536)` in the `embedding` column.
9. The `chunk_metadata` JSONB field MUST store at minimum: `{ "page_numbers": [<int>], "chunk_index": <int>, "source_filename": "<str>" }`.
10. Chunk inserts MUST be batched in a single `INSERT` statement (not one-by-one) to avoid N+1 database round trips.
11. After successful chunk insert, `document.chunk_count` MUST be set to the exact number of chunks inserted and `document.status` MUST be set to `ready` in the same database transaction.
12. The Celery task MUST retry on `openai.APIError` and `openai.RateLimitError` with a max of 3 retries and exponential backoff starting at 30 seconds: `countdown = 30 * (2 ** self.request.retries)`.
13. The Celery task MUST delete the tmp file at `file_path` after ingestion completes, whether it succeeds or fails (use a `finally` block).
14. `RetrievalService.retrieve(user_id, query, top_k=5)` MUST: embed the query string once using the same model and encoding; fetch all chunk embeddings for the given `user_id` from the database using `SELECT id, content, embedding, chunk_metadata, document_id FROM document_chunks WHERE user_id = :user_id`; compute cosine similarity in-process using `numpy`; return the top-k results sorted descending by score.
15. Every SQL query touching `documents` or `document_chunks` MUST include `WHERE user_id = :user_id` (or equivalent ORM filter). There MUST be no code path that queries chunks without a user_id filter.
16. The retrieve-debug endpoint MUST require authentication and MUST enforce user_id scoping — it MUST NOT accept a `user_id` override parameter.
17. The `GET /api/v1/documents/{document_id}` endpoint MUST return 404 if the document belongs to a different user (no 403).
18. The Celery task MUST update `document.updated_at` when setting status to `processing`, `ready`, or `failed`.
19. `OPENAI_API_KEY` MUST be read from `app.config.settings`; the task MUST raise `ImproperlyConfigured` (or equivalent) at startup if the key is blank.
20. The `ingest_document` task module MUST be registered in the `include=[]` list in `celery_app.py`.
21. All new endpoints MUST be mounted under the existing `/api` prefix via the router pattern used in `main.py`.

## Data model changes

### Migration `0004_add_chunk_count_to_documents.py`

**Alter table `documents`:**
```
chunk_count  INTEGER  NULL  DEFAULT NULL
```
- No index needed — this column is display-only, never filtered on.
- Migration is additive; no data backfill required (existing rows stay NULL).

### `document_chunks` table (existing, no changes)
Column reference for implementation:

| Column | Type | Nullable | Notes |
|---|---|---|---|
| id | UUID | NOT NULL | PK |
| document_id | UUID | NOT NULL | FK → documents.id CASCADE |
| user_id | UUID | NOT NULL | FK → users.id CASCADE; indexed |
| chunk_index | INTEGER | NOT NULL | 0-based position within document |
| content | TEXT | NOT NULL | raw chunk text |
| embedding | vector(1536) | NULL | set during ingestion |
| metadata | JSONB | NULL | page_numbers, chunk_index, source_filename |
| created_at | TIMESTAMPTZ | NOT NULL | server default NOW() |

Existing indexes on `document_chunks` (from migration 0001) must include an index on `user_id` — confirm at migration time. If missing, add in migration `0004`.

## API contracts

### `POST /api/v1/documents/upload`
- **Auth**: required (Clerk JWT via `clerk_auth` dependency)
- **Content-Type**: `multipart/form-data`
- **Request fields**:
  | Field | Type | Required | Validation |
  |---|---|---|---|
  | file | UploadFile | yes | MIME must be `application/pdf`; size ≤ 50 MB |
  | doc_type | str | no | must match `DocumentType` enum; default `"other"` |
  | ticker | str | no | max 20 chars |
  | filing_date | date (ISO 8601) | no | must be a valid date |
- **Response `202`**:
  ```json
  { "document_id": "<uuid>", "status": "pending" }
  ```
- **Response `422`**: FastAPI validation error body for field errors, plus:
  ```json
  { "detail": "file too large" }          // size > 50 MB
  { "detail": "file must be a PDF" }      // wrong MIME type
  ```
- **Response `401`**: missing or invalid JWT

---

### `GET /api/v1/documents`
- **Auth**: required
- **Query params**: none (pagination is out of scope)
- **Response `200`**:
  ```json
  {
    "documents": [
      {
        "id": "<uuid>",
        "filename": "apple_10k_2024.pdf",
        "doc_type": "10-K",
        "ticker": "AAPL",
        "filing_date": "2024-09-28",
        "status": "ready",
        "chunk_count": 142,
        "created_at": "2026-05-21T10:00:00Z"
      }
    ]
  }
  ```
- Returns only the authenticated user's documents, ordered by `created_at DESC`.

---

### `GET /api/v1/documents/{document_id}`
- **Auth**: required
- **Path param**: `document_id` (UUID)
- **Response `200`**: same fields as list item above, plus `error_message` (string or null)
- **Response `404`**: document not found or belongs to another user

---

### `POST /api/v1/documents/retrieve-debug`
- **Auth**: required
- **Content-Type**: `application/json`
- **Request body**:
  ```json
  { "query": "What was Apple's revenue in FY2024?", "top_k": 5 }
  ```
  `top_k` is optional; default 5, max 20.
- **Response `200`**:
  ```json
  {
    "results": [
      {
        "chunk_id": "<uuid>",
        "document_id": "<uuid>",
        "score": 0.891,
        "content": "Revenue for fiscal year 2024 was $391 billion...",
        "metadata": { "page_numbers": [42], "chunk_index": 87, "source_filename": "apple_10k_2024.pdf" }
      }
    ]
  }
  ```
- **Response `422`**: query is blank or top_k out of range

## Component and file structure

### Backend — new files
| File | Purpose |
|---|---|
| `backend/alembic/versions/0004_add_chunk_count_to_documents.py` | Adds `chunk_count INTEGER NULL` to `documents`; adds index on `document_chunks.user_id` if missing |
| `backend/app/tasks/ingestion.py` | Celery task `ingest_document(document_id: str, file_path: str)` — full ingestion pipeline |
| `backend/app/services/retrieval.py` | `RetrievalService` class with `retrieve(user_id, query, top_k)` method |
| `backend/app/api/v1/documents.py` | FastAPI router with all four endpoints |
| `backend/app/schemas/document.py` | Pydantic request/response models for document endpoints |
| `backend/app/tasks/__init__.py` | Empty init to make `tasks` a package |

### Backend — modified files
| File | Change |
|---|---|
| `backend/app/celery_app.py` | Add `"app.tasks.ingestion"` to `include=[]` |
| `backend/app/main.py` | Register the documents router under `/api` prefix |
| `backend/app/config.py` | Add `MAX_UPLOAD_BYTES: int = 50_000_000` setting |

### Tests — new files
| File | Purpose |
|---|---|
| `backend/tests/test_ingestion_task.py` | Unit tests for the Celery task (mocked OpenAI, real chunking logic) |
| `backend/tests/test_documents_api.py` | Integration tests for upload, list, get, retrieve-debug endpoints |
| `backend/tests/test_retrieval_service.py` | Unit tests for `RetrievalService` with synthetic embeddings |

## External dependencies

| Dependency | Purpose | If unavailable | Notes |
|---|---|---|---|
| `openai` Python SDK | Embedding API calls (`text-embedding-3-small`) | Task fails and retries up to 3×; document marked `failed` after exhaustion | Already in pyproject.toml as a dependency for chat; confirm version ≥ 1.0 |
| `pypdf` | PDF text extraction | Task fails immediately; document marked `failed` | Add to `pyproject.toml` if not present |
| `tiktoken` | Token counting for chunking | Task fails immediately | Add to `pyproject.toml` |
| `numpy` | Cosine similarity computation | `RetrievalService` fails | Add to `pyproject.toml` if not present |
| `pgvector` SQLAlchemy extension | `Vector(1536)` column type | Already in use in `document.py` | No change needed |

## Testing plan

### Unit tests — `test_ingestion_task.py`
- Chunking splits a 2000-token string into the expected number of chunks with the correct overlap
- Chunk at the boundary of 800 tokens does not exceed 800 tokens
- Empty extracted text (< 50 chars) triggers `failed` status, no chunks inserted
- OpenAI API error triggers retry; after 3 retries status is `failed`
- Chunk batch insert writes the correct number of rows (mock DB session)
- `chunk_metadata` JSONB contains `page_numbers`, `chunk_index`, `source_filename`

### Unit tests — `test_retrieval_service.py`
- Returns empty list when user has no chunks
- Returns results sorted descending by cosine similarity
- Returns at most `top_k` results even when more chunks exist
- Never returns chunks belonging to a different `user_id` (insert chunks for two users, query one)

### Integration tests — `test_documents_api.py`
- `POST /upload` with a valid PDF → 202, document row created with `status=pending`
- `POST /upload` with a file whose first 4 bytes are not `%PDF` → 422 `"file must be a PDF"`
- `POST /upload` with a 51 MB payload → 422 `"file too large"`
- `POST /upload` without auth → 401
- `GET /documents` returns only the authenticated user's documents
- `GET /documents/{id}` for own document → 200
- `GET /documents/{id}` for another user's document → 404
- `POST /retrieve-debug` with a valid query after ingestion → 200 with non-empty results
- `POST /retrieve-debug` with blank query → 422

### Manual verification
1. Start `docker-compose up postgres redis`, run `alembic upgrade head`, confirm `chunk_count` column exists in `documents`.
2. Start the Celery worker: `celery -A app.celery_app worker --loglevel=info`.
3. Start the FastAPI server: `uvicorn app.main:app --reload`.
4. Upload a real 10-K PDF: `curl -X POST http://localhost:8000/api/v1/documents/upload -H "Authorization: Bearer <token>" -F "file=@apple_10k.pdf" -F "doc_type=10-K" -F "ticker=AAPL"` → expect 202.
5. Poll `GET /api/v1/documents/{id}` until `status=ready`.
6. Call `POST /api/v1/documents/retrieve-debug` with a question that should appear in the document; verify top result has score > 0.7 and content is coherent.
7. Attempt the same retrieve call with a second user's JWT; confirm 0 results returned.

## Observability

### Logs (structured, via Python `logging`)
| Event | Level | Fields |
|---|---|---|
| Upload received | INFO | `document_id`, `filename`, `user_id`, `file_size_bytes` |
| Task started | INFO | `document_id`, `user_id` |
| Extraction complete | INFO | `document_id`, `page_count`, `char_count` |
| Chunking complete | INFO | `document_id`, `chunk_count` |
| Embedding batch complete | INFO | `document_id`, `chunks_embedded` |
| Task succeeded | INFO | `document_id`, `chunk_count`, `elapsed_seconds` |
| Task failed (retriable) | WARNING | `document_id`, `error`, `retry_number` |
| Task failed (final) | ERROR | `document_id`, `error` |
| Retrieval called | DEBUG | `user_id`, `query_length`, `top_k` |
| Retrieval complete | DEBUG | `user_id`, `results_count`, `top_score` |

### Healthy vs unhealthy
- **Healthy**: Celery worker processing `ingest_document` tasks; documents transition from `pending → processing → ready` within 60 seconds for a typical 5 MB PDF; retrieval returns results with scores > 0.6 for on-topic queries.
- **Unhealthy**: Documents stuck in `pending` (worker down or task not registered); documents in `failed` state with `error_message` containing `openai`; retrieval returning 0 results for a user with `status=ready` documents (embedding mismatch or isolation bug).

## Risks and open questions

1. **Tmp file lifetime**: The API writes the PDF to `/tmp/{document_id}.pdf` and the task deletes it in a `finally` block. If the worker process crashes before reaching `finally`, the tmp file leaks. At the scale of this application this is acceptable; a future improvement is to write to object storage (S3/GCS) instead and have the task receive only the object key.
2. **In-process cosine similarity**: Loading all chunk embeddings for a user into memory and computing similarity with numpy is fast for hundreds of chunks but will degrade at tens of thousands. At that scale the query should be pushed into pgvector using `<=>` operator with an HNSW index. Deferred — the `document_chunks` table does not have an HNSW index yet; add it when retrieval latency becomes measurable.
3. **Token counting accuracy**: `tiktoken` `cl100k_base` is the correct encoding for `text-embedding-3-small`. If Anthropic models are added later, token counting will need to be per-model.
4. **Chunk overlap across pages**: The sliding-window chunker operates on concatenated full-document text. A chunk may span a page boundary, making `page_numbers` a list rather than a single integer. The metadata schema already accommodates this with an array.
