# Spec: Hybrid Search

## Goal
Improve retrieval precision for exact financial terms (e.g. "EBITDA margin", clause numbers) by combining PostgreSQL full-text search (BM25) with existing pgvector cosine similarity, so keyword-exact chunks rank above semantically adjacent but textually different chunks.

## Background
`RetrievalService.retrieve()` in `backend/app/services/retrieval.py` currently runs a single pgvector HNSW cosine distance query and passes candidates to a cross-encoder reranker (`ms-marco-MiniLM-L-6-v2`). Cosine similarity works well for semantic queries but can fail when a user asks for a specific term or figure that happens to appear verbatim in only one chunk — the embedding of "EBITDA margin" and "operating profitability" are close, so the exact-match chunk may not be in the candidate pool fed to the reranker at all.

PostgreSQL 15 has native full-text search with `tsvector`/`tsquery` and `ts_rank_cd` scoring. Adding a generated `tsvector` column to `document_chunks` enables BM25-style ranking without any new infrastructure. A linear combination of the two scores, controlled by `alpha`, lets the reranker see a candidate set that is enriched with keyword matches.

Prior decisions that constrain this design:
- Cross-encoder reranker already exists and runs last; hybrid scoring feeds *into* it, not after it.
- `ChunkResult.similarity_score` is consumed by the synthesizer and citation endpoint — its meaning changes to "hybrid score" but its type and range stay the same.
- No changes to tool interfaces, agent files, or embedding pipeline.

---

## Scope

### In scope
- Alembic migration `0010` adding a `GENERATED ALWAYS AS (to_tsvector('english', content)) STORED` column (`content_tsv`) and a GIN index to `document_chunks`.
- `content_tsv` mapped column on the `DocumentChunk` SQLAlchemy model.
- `HYBRID_SEARCH_ALPHA: float = 0.7` setting in `app/config.py`.
- Hybrid retrieval logic in `RetrievalService.retrieve()`: run vector query + FTS query, merge by `chunk_id`, compute `hybrid_score = alpha * vector_score + (1-alpha) * bm25_score_normalized`, pass merged candidates to the existing reranker.
- Fallback: if FTS query matches zero chunks, skip merging and proceed with pure vector candidates (equivalent to alpha=1.0).
- Debug log events updated to record whether hybrid mode was active and how many FTS candidates were found.
- New unit and integration tests for hybrid scoring, FTS-only fallback, and FTS-absent fallback.

### Out of scope
- BM25 via Elasticsearch, Tantivy, or any external service.
- User-configurable alpha (no API parameter, no per-conversation setting).
- Hybrid search for the web/news search tool — only document chunk retrieval.
- Changes to the embedding pipeline (chunking, ingestion, `text-embedding-3-small`).
- Language configuration for `to_tsvector` — `'english'` is hardcoded.
- Backfill strategy for chunks inserted before this migration (the `GENERATED ALWAYS AS STORED` definition causes PostgreSQL to compute `content_tsv` for all existing rows automatically when the migration runs).

---

## User flow

### Happy path — keyword-exact query
1. User asks "What was the EBITDA margin for Q3?"
2. Agent calls `retrieval_tool` → `RetrievalService.retrieve()`.
3. Service embeds the query with `text-embedding-3-small`.
4. **Vector query**: fetch up to `top_k * 3` candidates ordered by cosine distance.
5. **FTS query**: parse query with `websearch_to_tsquery('english', ...)`, run against `content_tsv`, fetch up to `top_k * 3` candidates ordered by `ts_rank_cd` descending. At least one row matches because the exact phrase appears in a chunk.
6. Merge the two candidate sets by `chunk_id`. Normalize FTS scores to [0, 1] (min-max over the FTS result set). Compute `hybrid_score = 0.7 * vector_score + 0.3 * bm25_normalized` for each chunk. Chunks present in only one set get 0.0 for the missing score.
7. Sort descending by `hybrid_score`, keep up to `candidate_k` entries, pass to cross-encoder reranker.
8. Return top_k reranked results; the EBITDA chunk appears first.

### Happy path — semantic-only query
1. User asks "What is the company's revenue growth strategy?"
2. FTS likely returns zero rows (no word-exact match in the corpus).
3. Service detects `fts_count=0`, logs `hybrid_search_applied=False`, skips merge.
4. Pure vector candidates pass directly to cross-encoder reranker. Behavior is identical to today.

### Edge cases
- **No chunks at all**: vector query returns 0 rows → return `[]` immediately (same as today).
- **FTS parse error** (e.g., malformed query string): catch the exception, log `fts_query_failed`, proceed with pure vector candidates.
- **All FTS scores equal** (single matching chunk): min-max normalization produces `bm25_normalized=1.0` for that chunk; all others get 0.0. Hybrid boost is applied correctly.
- **`websearch_to_tsquery` returns NULL** (empty or stop-word-only input): the `@@` operator short-circuits to false for all rows; FTS result set is empty; fall back to pure vector.

---

## Detailed requirements

1. Migration `0010` must add column `content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED` to `document_chunks`. This must be a stored (not virtual) generated column so it is populated at write time.
2. Migration `0010` must create a GIN index `idx_document_chunks_content_tsv` on `document_chunks (content_tsv)`.
3. Migration `0010` `downgrade()` must drop the index then drop the column in that order.
4. The `DocumentChunk` SQLAlchemy model must expose `content_tsv` as a mapped column of type `TSVECTOR` with `sa.Computed("to_tsvector('english', content)", persisted=True)`. The column must be `deferred=True` so it is not loaded in the standard ORM queries that don't use it.
5. `Settings` must have a field `HYBRID_SEARCH_ALPHA: float = 0.7`. It must accept values in (0.0, 1.0] and be configurable via the `.env` file.
6. `RetrievalService.retrieve()` must run a FTS query using `websearch_to_tsquery('english', query)` against the `content_tsv` column, filtered to the same `user_id` and `conversation_id` scope as the vector query.
7. The FTS query must return at most `candidate_k` results ordered by `ts_rank_cd(content_tsv, tsquery)` descending.
8. FTS scores must be min-max normalized to [0.0, 1.0] over the fetched result set before mixing. If only one FTS result exists, its normalized score is 1.0.
9. When the FTS result set is non-empty, the hybrid score for each chunk must be computed as: `hybrid_score = settings.HYBRID_SEARCH_ALPHA * vector_score + (1.0 - settings.HYBRID_SEARCH_ALPHA) * bm25_normalized`. `vector_score` defaults to 0.0 for chunks not in the vector result set; `bm25_normalized` defaults to 0.0 for chunks not in the FTS result set.
10. When FTS returns zero results, `retrieve()` must skip hybrid merge entirely and proceed with the pure vector candidates unchanged. The field `similarity_score` in returned `ChunkResult` objects must reflect pure cosine similarity in this case.
11. Exceptions raised during the FTS query (e.g. `ProgrammingError`, network error) must be caught, logged at WARNING level with the exception string, and must result in fallback to pure vector candidates. The exception must not propagate to the caller.
12. `ChunkResult.similarity_score` stores the hybrid score when hybrid mode is active, and the cosine similarity (unchanged) when falling back to pure vector. No schema change to `ChunkResult`.
13. The `retrieval_complete` structlog event must include boolean field `hybrid_search_applied` and integer field `fts_candidate_count`.
14. No changes may be made to tool-layer interfaces (`retrieve_tool`, `RetrieveRequest`, `RetrieveResponse`), agent files (`executor.py`, `synthesizer.py`), or embedding pipeline code.
15. All existing retrieval tests must continue to pass without modification.
16. A new test must verify that a chunk containing the exact phrase "EBITDA margin" ranks first when the query is "EBITDA margin" and a competing chunk contains only semantically similar language.

---

## Data model changes

### `document_chunks` — add column and index

```sql
-- Applied in migration 0010 upgrade()
ALTER TABLE document_chunks
  ADD COLUMN content_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX idx_document_chunks_content_tsv
  ON document_chunks USING GIN (content_tsv);

-- Applied in migration 0010 downgrade()
DROP INDEX IF EXISTS idx_document_chunks_content_tsv;
ALTER TABLE document_chunks DROP COLUMN IF EXISTS content_tsv;
```

**Column details**

| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `content_tsv` | `tsvector` | NOT NULL (generated) | `to_tsvector('english', content)` |

**Why the GIN index**: `ts_rank_cd` does not use indexes, but the `@@` match operator does. Without a GIN index, every FTS query performs a sequential scan of all chunks belonging to the user × conversation. With the GIN index, only matching postings are visited.

**Migration order**: This is migration `0010`, revises `0009`. No other migrations depend on it.

---

## API contracts

No new or modified API endpoints. `RetrievalService.retrieve()` is an internal Python interface; its signature is unchanged:

```python
async def retrieve(
    self,
    db: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    query: str,
    top_k: int = 5,
) -> list[ChunkResult]:
```

The existing `/api/debug/retrieve` endpoint (accepts `RetrieveDebugRequest`, returns `RetrieveDebugResponse`) is unchanged. `ChunkResult.similarity_score` will reflect the hybrid score when hybrid mode is active, but the schema is identical.

---

## Component and file structure

### Backend — modified
- `backend/app/config.py` — add `HYBRID_SEARCH_ALPHA: float = 0.7` field to `Settings`.
- `backend/app/models/document.py` — add `content_tsv` mapped column to `DocumentChunk` using `sa.Computed` and `TSVECTOR` dialect type; mark `deferred=True`.
- `backend/app/services/retrieval.py` — add `_fts_query()` private method, integrate hybrid merge and normalization into `retrieve()`, update log events.

### Backend — created
- `backend/alembic/versions/0010_hybrid_search_tsvector.py` — Alembic migration adding the generated column and GIN index.

### Tests — modified
- `backend/tests/test_retrieval_service.py` — add new test cases (see Testing plan). Existing tests unchanged.

---

## External dependencies

| Dependency | Role | Availability risk |
|------------|------|-------------------|
| PostgreSQL 12+ | Required for `GENERATED ALWAYS AS ... STORED` | Already a project dependency (pg15). No risk. |
| `websearch_to_tsquery` | Parses natural-language FTS query strings robustly | Available since PostgreSQL 11. No risk. |
| `ts_rank_cd` | Computes BM25-like score per matching chunk | Built-in PostgreSQL function. No risk. |
| `pgvector` extension | Existing HNSW index, unchanged | Already enabled. No risk. |

No new Python packages required.

---

## Testing plan

### Unit tests (mocked DB, `backend/tests/test_retrieval_service.py`)

These tests require a real PostgreSQL instance with the `pgvector` extension and migration `0010` applied (same pattern as existing tests).

| Test | What it verifies |
|------|-----------------|
| `test_hybrid_exact_term_ranks_first` | Insert two chunks: one containing "EBITDA margin", one containing "operating profitability". Use identical embeddings for both. Query "EBITDA margin". Assert the EBITDA chunk is `results[0]`. |
| `test_hybrid_fts_fallback_no_matches` | Insert chunks with no tokens matching the query. Assert `hybrid_search_applied=False` path is taken and results are non-empty (vector-only). |
| `test_hybrid_score_range` | Hybrid score for every returned chunk must be in [0.0, 1.0]. |
| `test_hybrid_fts_exception_falls_back` | Patch the FTS execute call to raise `Exception("db error")`. Assert results are still returned (pure vector), no exception propagates. |
| `test_hybrid_single_fts_match_normalized` | Insert one chunk that matches FTS, several that do not. The FTS-matching chunk's bm25_normalized is 1.0 (verified indirectly via it ranking higher than semantic-only chunks with same embedding distance). |
| `test_hybrid_alpha_respected` | Temporarily set `settings.HYBRID_SEARCH_ALPHA = 1.0`. Insert one FTS-matching and one vector-close chunk. Assert the vector-close chunk wins (alpha=1.0 means BM25 is ignored). Reset alpha. |

### Integration / regression
- All 8 existing tests in `test_retrieval_service.py` must pass against a database with migration `0010` applied. Their behavior should be unchanged because they use queries where FTS either returns no results (fallback path) or the cross-encoder produces the same final ranking.

### Manual verification
1. `alembic upgrade head` on a development database — verify no SQL errors.
2. `\d document_chunks` in `psql` — confirm `content_tsv` column is present and `idx_document_chunks_content_tsv` exists with `GIN` type.
3. Insert a document containing the phrase "EBITDA margin" via the upload flow. Run `SELECT content_tsv FROM document_chunks LIMIT 1;` and confirm it is non-null.
4. Hit `/api/debug/retrieve` with `query="EBITDA margin"` — confirm the EBITDA-containing chunk is `results[0]`.
5. Hit `/api/debug/retrieve` with `query="what is the revenue growth strategy"` — confirm results are still returned and `similarity_score` values are in [0, 1].

---

## Observability

### Log events (structlog)

| Event | Level | Fields added |
|-------|-------|-------------|
| `retrieval_called` | DEBUG | unchanged |
| `fts_query_failed` | WARNING | `error: str` |
| `hybrid_search_applied` | DEBUG | `fts_candidate_count: int`, `vector_candidate_count: int`, `merged_candidate_count: int` |
| `retrieval_complete` | DEBUG | `hybrid_search_applied: bool`, `fts_candidate_count: int` (added to existing event) |

### Healthy state
- `hybrid_search_applied=True` for queries containing financial keywords.
- `fts_candidate_count` between 1 and `candidate_k`.
- `top_score` in [0.0, 1.0].

### Unhealthy state
- `fts_query_failed` warnings appearing consistently — indicates the GIN index may be missing or the `tsvector` column was not created.
- `hybrid_search_applied=False` for all queries despite keyword-heavy input — indicates FTS query is not matching (possible `websearch_to_tsquery` producing NULL for the input).

---

## Risks and open questions

**Risks**

- **Migration on large tables**: adding a `GENERATED ALWAYS AS STORED` column on a large `document_chunks` table requires a full table rewrite in PostgreSQL. For development this is not a concern; for production deployments with millions of rows, a `CONCURRENTLY` approach is not available for generated columns — plan for a maintenance window.
- **FTS language mismatch**: `'english'` stemming will incorrectly process non-English documents (e.g. French filings). This is acceptable for the current user base but will need revisiting if non-English documents are ingested.
- **Score distribution skew**: `ts_rank_cd` returns very small values (e.g. 0.003) for short queries against long documents. Min-max normalization corrects the scale but makes a single weak match look like `bm25=1.0`. If this causes ranking regressions, consider switching to the `ts_rank` variant or clipping at a minimum threshold.

**Open questions**

- Should `HYBRID_SEARCH_ALPHA` be validated (e.g. must be in (0.0, 1.0])? Deferred — use a Pydantic validator if mis-configuration causes support issues.
- Should the `candidate_k` multiplier for the FTS query be the same as for the vector query (`top_k * 3`)? Assumed yes — can be tuned independently if benchmarks suggest otherwise.
- Should `websearch_to_tsquery` be replaced with `plainto_tsquery` for simpler input handling? `websearch_to_tsquery` is safer for arbitrary user input; keep it.
