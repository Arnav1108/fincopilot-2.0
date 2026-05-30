# Spec: Multi-Document Synthesis

## Goal
Inject source document filename into every retrieved chunk so the synthesizer can attribute claims to the specific filing or transcript they came from.

## Background
Today, `RetrievalService.retrieve()` builds `ChunkResult` objects from `DocumentChunk` rows without joining the parent `Document` table. The `ChunkResult.metadata` dict carries whatever was stored in the `chunk_metadata` JSONB column at ingestion time, but never the document's filename. As a result, the synthesizer's RAG prompt presents numbered chunks with no indication of which file they came from. When a conversation has two documents — e.g. "Apple_2024_10K.pdf" and "Apple_Q3_2024_earnings.pdf" — the synthesizer cannot produce "According to the 2024 10-K… but the Q3 earnings call said…"; it can only say "According to the documents…".

The `Document` model already carries `filename` and `doc_type`. The `DocumentChunk.document_id` FK already links every chunk to its parent document. The enrichment requires only: (1) a JOIN at retrieval time, (2) a new field on `ChunkResult`, and (3) updated prompt and chunk rendering in the synthesizer.

Prior decisions that constrain the design:
- No new DB tables or migrations (constraint from the user).
- `chunk_metadata` JSONB must not be modified at storage time — enrichment is retrieval-time only.
- Single-document flows must remain unchanged in behavior.

## Scope

### In scope
- Add `document_filename: str | None` and `document_type: str | None` fields to `ChunkResult`.
- Modify `RetrievalService.retrieve()` to JOIN `Document` and populate the two new fields.
- Update the RAG chunk rendering in `synthesizer_node` to include the filename label for each chunk.
- Update `_SYSTEM_PROMPT_RAG` to instruct the model to cite by document name instead of generic [1], [2] references.
- Update `_SYSTEM_PROMPT_RAG` citation rule to also mention citing by document name.
- Unit tests for the enriched `ChunkResult` construction and synthesizer prompt building.
- Integration test for the retrieval JOIN.

### Out of scope
- Per-chunk citation UI (click to highlight source chunk in viewer).
- Cross-conversation document access.
- Document-level relevance scoring (ranking whole documents before chunks).
- Changes to chunk storage schema or ingestion pipeline.
- Changing `chunk_metadata` JSONB contents at write time.
- `RetrieveDebugResponse` API response changes (the debug endpoint already returns `document_id`; adding `document_filename` there is a follow-up).

## User flow

### Happy path — multiple documents
1. User uploads "Apple_2024_10K.pdf" and "Apple_Q3_2024_earnings.pdf" in the same conversation.
2. User asks: "What did Apple say about AI investment?"
3. Agent routes to RAG path, calls `RetrievalService.retrieve()`.
4. Retrieval JOINs `Document` and returns top-k `ChunkResult` objects, each with `document_filename` set (e.g. "Apple_2024_10K.pdf" or "Apple_Q3_2024_earnings.pdf").
5. Synthesizer renders each chunk as `[1] Apple_2024_10K.pdf: <content>` and `[2] Apple_Q3_2024_earnings.pdf: <content>`.
6. System prompt instructs model to cite by filename.
7. Model produces: "According to the 2024 10-K, Apple allocated $X billion… In the Q3 earnings call, Tim Cook emphasized…"

### Happy path — single document
1. User uploads one document.
2. RAG retrieval returns chunks all from the same filename.
3. Synthesizer renders normally with filename label. Model response may or may not mention the filename explicitly — that's fine; the prompt no longer forbids it.
4. Behavior is functionally identical to today from the user's perspective (citation style may improve).

### Edge case — chunk has no document join match
- Defensive: if the LEFT JOIN returns `None` for `Document` (document deleted between chunk creation and retrieval), `document_filename` is `None` and `document_type` is `None`.
- Synthesizer rendering omits the filename label and falls back to `[{i}] {content}` for that chunk.

### Edge case — all chunks from the same document
- The model sees repeated filename labels. This is harmless — the model is still instructed to cite by filename and will do so naturally.

### Edge case — no chunks retrieved
- Existing behavior unchanged: synthesizer falls through to tool-result or LLM-only path.

## Detailed requirements

1. `ChunkResult` (in `app/schemas/document.py`) MUST add two new optional fields: `document_filename: str | None = None` and `document_type: str | None = None`.
2. The new fields MUST default to `None` so existing call sites that construct `ChunkResult` without them remain valid (backwards compatible).
3. `RetrievalService.retrieve()` MUST change its `select()` to also select `Document` via a JOIN on `DocumentChunk.document_id == Document.id`.
4. The JOIN MUST be a LEFT OUTER JOIN so that chunks whose parent document has been deleted do not disappear from retrieval results.
5. After the JOIN, `ChunkResult` construction MUST populate `document_filename` from `Document.filename` (or `None` if document is absent) and `document_type` from `Document.doc_type.value` (or `None`).
6. `_SYSTEM_PROMPT_RAG` MUST be updated to instruct the synthesizer to cite by document filename (e.g. "In [filename], …") in addition to or instead of generic numbered citations.
7. The RAG chunk rendering block in `synthesizer_node` (currently line 198: `f"[{i}] {chunk['content']}"`) MUST include the filename when present, formatted as `f"[{i}] ({filename}) {content}"`. When `document_filename` is `None`, fall back to `f"[{i}] {content}"`.
8. The synthesizer MUST accept `ChunkResult` objects as dicts (existing serialization via `state["reranked_chunks"]`). The two new fields MUST be present in the dict representation when serialized. This requires verifying the state serialization path.
9. No changes may break the `reranked_chunks` list when it is empty — the existing early-return guard at synthesizer line 148 must remain intact.
10. The change MUST NOT alter how `chunk_metadata` is stored — only retrieval enrichment is permitted.
11. All existing tests that construct `ChunkResult` with positional args or without the new fields MUST continue to pass without modification.
12. A log line `retrieval_enriched` at DEBUG level MUST be emitted when at least one chunk has a non-None `document_filename`, including a count of distinct filenames.

## Data model changes

No new tables. No migrations.

### `ChunkResult` schema change (not a DB change)

**File**: `app/schemas/document.py`

```python
class ChunkResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    similarity_score: float
    content: str
    metadata: dict | None
    document_filename: str | None = None   # NEW
    document_type: str | None = None       # NEW
```

Both fields default to `None` so existing construction sites without them continue to work.

### `retrieval.py` query change

The current `select(DocumentChunk, distance_expr)` becomes `select(DocumentChunk, Document, distance_expr)` with an `outerjoin`:

```python
from app.models.document import Document, DocumentChunk

stmt = (
    select(DocumentChunk, Document, distance_expr)
    .outerjoin(Document, DocumentChunk.document_id == Document.id)
    .where(...)
    .order_by(distance_expr)
    .limit(candidate_k)
)
```

Result unpacking changes from `(chunk, distance)` to `(chunk, doc, distance)`.

`ChunkResult` construction changes from:

```python
ChunkResult(
    chunk_id=chunk.id,
    document_id=chunk.document_id,
    similarity_score=float(1.0 - distance),
    content=chunk.content,
    metadata=chunk.chunk_metadata,
)
```

to:

```python
ChunkResult(
    chunk_id=chunk.id,
    document_id=chunk.document_id,
    similarity_score=float(1.0 - distance),
    content=chunk.content,
    metadata=chunk.chunk_metadata,
    document_filename=doc.filename if doc else None,
    document_type=doc.doc_type.value if doc else None,
)
```

## API contracts

No new or modified HTTP endpoints. The debug endpoint `POST /api/v1/documents/retrieve-debug` returns `RetrieveDebugResponse` which contains `list[ChunkResult]`. The two new optional fields will automatically appear in the JSON response once added to `ChunkResult` — this is additive and not a breaking change.

No auth, rate-limit, or request-schema changes.

## Component and file structure

### Backend — modified files

| File | Change |
|------|--------|
| `app/schemas/document.py` | Add `document_filename: str \| None = None` and `document_type: str \| None = None` to `ChunkResult`. |
| `app/services/retrieval.py` | Change `select()` to LEFT OUTER JOIN `Document`; unpack three-column rows; populate new `ChunkResult` fields; add `retrieval_enriched` debug log. |
| `app/agent/synthesizer.py` | Update `_SYSTEM_PROMPT_RAG` with filename citation instruction; update chunk rendering to include filename label when present. |

### Tests — new/modified files

| File | Change |
|------|--------|
| `backend/tests/services/test_retrieval_enrichment.py` | New. Unit tests for the enriched `ChunkResult` construction (mock DB rows). |
| `backend/tests/agent/test_synthesizer_prompt.py` | New or extended. Tests for chunk rendering with and without filename. |

No frontend changes. No config changes. No new dependencies.

## External dependencies

None. All changes use existing SQLAlchemy async patterns and existing OpenAI client.

## Testing plan

### Unit tests

**`test_retrieval_enrichment.py`**
- Given a `(DocumentChunk, Document, distance)` row, assert `ChunkResult.document_filename` equals `Document.filename`.
- Given a `(DocumentChunk, None, distance)` row (LEFT JOIN null), assert `document_filename` is `None`.
- Assert `document_type` is the string value (not enum member) of `Document.doc_type`.

**`test_synthesizer_prompt.py`**
- Given a chunk dict with `document_filename="Apple_2024_10K.pdf"` and `content="Revenue grew..."`, assert the rendered chunk string contains `"(Apple_2024_10K.pdf)"`.
- Given a chunk dict with `document_filename=None`, assert the rendered chunk string is `"[1] Revenue grew..."` (no filename label).
- Assert `_SYSTEM_PROMPT_RAG` contains the word "filename" or "document name" (verifies the instruction was added).

### Integration tests

- Extend or add to the existing retrieval integration path: create two `Document` rows and associated `DocumentChunk` rows with embeddings, call `RetrievalService.retrieve()`, assert that returned `ChunkResult` objects carry distinct `document_filename` values matching each parent document.

### Manual verification

1. Upload two PDF files in a single conversation (e.g. two 10-K filings).
2. Ask a question whose answer spans both documents.
3. Verify the response text names both files explicitly (e.g. "According to Apple_2024_10K.pdf…" and "According to Apple_Q3_2024_earnings.pdf…").
4. Upload a single PDF and ask a question — verify response is coherent and not broken by the new prompt instruction.
5. Call `POST /api/v1/documents/retrieve-debug` and confirm `document_filename` and `document_type` appear in the JSON response.

## Observability

**New log line** (DEBUG): `retrieval_enriched` emitted at the end of `retrieve()` when at least one chunk carries a filename, with fields:
- `distinct_document_count: int` — number of unique document IDs in the result set.
- `distinct_filename_count: int` — number of unique filenames (may differ if filenames collide).
- `results_count: int` — total chunks returned.

**Existing logs unchanged**: `retrieval_called`, `retrieval_complete`, `reranker_applied`, `reranker_failed` all remain.

**Healthy state**: `retrieval_enriched` appears with `distinct_filename_count >= 1` for every RAG invocation with documents present.

**Unhealthy state**: `document_filename: null` appearing for chunks that should have a parent document indicates the LEFT JOIN missed rows — likely a cascaded delete or FK violation. This can be detected by grepping structured logs for `document_filename=null` alongside a non-null `document_id`.

## Risks and open questions

**Risk: State serialization of `ChunkResult`**. The `reranked_chunks` list in `AgentState` is stored as a list of plain dicts (not `ChunkResult` objects) by the time it reaches the synthesizer. Verify that the executor/planner node that populates `reranked_chunks` serializes `ChunkResult` via `.model_dump()` and that the new fields appear in that dict. If they are serialized elsewhere (e.g. `dict(chunk)` without Pydantic), the new fields may be silently dropped. This must be confirmed before implementation.

**Risk: `doc_type` enum serialization**. `DocumentType` is a `str` enum. `doc.doc_type.value` will be a plain string like `"10-K"`. Confirm this is what is wanted, not the enum member name (`"filing_10k"`).

**Open question: citation format in the prompt**. The spec says to cite by document name. Should the model use the raw filename (e.g. `Apple_2024_10K.pdf`) or a cleaned label derived from `doc_type` and `ticker` (e.g. "Apple 10-K")? The simplest implementation uses the raw filename. A future iteration could construct a display label from `ticker + doc_type + filing_date` for cleaner prose — deferred out of scope.

**Assumption**: `Document.filename` is the original upload filename (e.g. `"Apple_2024_10K.pdf"`), not a server-side path or UUID. This is confirmed by the `Document` model where `filename: Mapped[str]` is set from the upload. If the filename contains a path separator, the rendering should strip to basename — add a `os.path.basename()` call defensively.
