# Spec: Earnings Comparison

## Goal
Enable users to compare the content of two uploaded earnings documents side-by-side, with each retrieved chunk tagged by its source document and the synthesizer producing a structured diff with clear attribution.

## Background
Today the `document_retrieval` tool runs a single cosine search across all `DocumentChunk` rows scoped to a `(user_id, conversation_id)` pair. When a user uploads two earnings PDFs — e.g. Apple Q2 2024 and Q3 2024 — and asks a comparison question, the retrieval has two problems:

1. **No per-document targeting.** The single cosine query has no concept of "retrieve from document A" vs "retrieve from document B." Whichever document has more semantically similar text will dominate the top-k results; the minority document may get zero representation.
2. **Attribution is stripped before synthesis.** `DocumentChunk.document_id` flows through `ChunkResult.document_id` and ends up in `ChunkDict.metadata["document_id"]`, but the synthesizer builds chunk lines as `"[i] {content}"` — the LLM never sees which document each chunk came from and cannot produce cross-document comparison prose.

Additionally, the planner and tool selector receive `has_documents: true` but no list of which documents are available, so they cannot issue targeted per-document retrieval calls even if they wanted to.

Prior decisions that constrain the design:
- Synthesizer does all diff reasoning — no new LLM calls beyond what synthesizer already makes.
- No new DB tables.
- The agent pipeline runs retrieval steps sequentially (executor already handles multiple `document_retrieval` steps).

## Scope

### In scope
1. Add `doc_ids: list[uuid.UUID] | None` filter to `DocumentRetrievalInput` and `RetrievalService.retrieve()` so retrieval can be scoped to specific documents.
2. Join the `documents` table in retrieval to populate `doc_filename`, `doc_ticker`, `doc_type`, and `doc_filing_date` into `ChunkResult.metadata`, giving every returned chunk a source label.
3. Add `available_documents: list[dict]` to `AgentState` so the planner and tool selector know which documents exist in the conversation.
4. Populate `available_documents` in `chat.py` alongside the existing `has_uploaded_documents` query.
5. Update the planner system prompt to receive the document list and issue two separate `document_retrieval` steps (each with a `doc_ids` filter) when the query compares two named documents.
6. Update the tool selector system prompt the same way for simple two-document comparison queries.
7. Update the synthesizer to render each chunk as `[i] [Source: <doc_label>] <content>` so the LLM sees attribution.
8. Add a comparison instruction to `_SYSTEM_PROMPT_RAG` so the LLM structures its answer when chunks from multiple source documents are present.
9. Validate `doc_ids` values in the executor against `available_documents` before passing to the tool (drops hallucinated or out-of-scope UUIDs silently).

### Out of scope
- Automatic document pairing — users must upload both documents; the agent picks which ones to query based on filenames.
- Comparing more than two documents at once.
- Structured financial diff in table format — natural language diff only.
- Cross-conversation document comparison.
- Any new frontend UI for selecting which documents to compare.
- New database tables or migrations.
- New LLM calls beyond what the synthesizer already makes.

## User flow

### Happy path
1. User uploads `apple_q2_2024.pdf` and `apple_q3_2024.pdf` to the conversation.
2. Both documents process to `status = ready`; `available_documents` will list both on the next turn.
3. User asks: *"How did Apple's revenue guidance change between Q2 and Q3?"*
4. Router classifies the query as `complex` (comparison + multiple documents).
5. Planner receives the query with `[has_documents: true]` and an `available_documents` block listing both docs with their UUIDs and filenames.
6. Planner emits two retrieval steps:
   - `{"tool_name": "document_retrieval", "input": {"query": "Apple revenue guidance", "doc_ids": ["<q2-uuid>"]}}`
   - `{"tool_name": "document_retrieval", "input": {"query": "Apple revenue guidance", "doc_ids": ["<q3-uuid>"]}}`
7. Executor runs both retrieval steps sequentially. Each calls `retrieval_service.retrieve(doc_ids=[...])`.
8. Retrieval service adds `WHERE document_chunks.document_id IN (doc_ids)` to the SQL and joins `documents` to annotate each `ChunkResult.metadata` with `doc_filename`, `doc_ticker`, `doc_type`, `doc_filing_date`.
9. `_normalize_chunks` merges these into `ChunkDict.metadata`.
10. Synthesizer receives all chunks. It renders each as `[1] [Source: apple_q2_2024.pdf] <content>`, `[2] [Source: apple_q3_2024.pdf] <content>`, etc.
11. The comparison instruction in `_SYSTEM_PROMPT_RAG` tells the LLM to address each document separately then summarize the difference.
12. Synthesizer streams tokens: *"In the Q2 2024 filing, Apple guided for... In contrast, the Q3 2024 filing indicates..."*
13. `done` SSE event fires. User sees a sourced, attributed comparison response.

### Edge cases and error states
- **Only one document uploaded**: planner has no second document to target. It issues a single `document_retrieval` step with no `doc_ids` filter, normal RAG path runs.
- **Comparison question with no documents**: existing `_is_document_specific` guard fires; user gets "No documents uploaded yet."
- **`doc_ids` filter returns zero chunks for one document**: executor proceeds with empty chunk list for that step; synthesizer notes "no relevant information found in [doc_label] for this topic."
- **LLM hallucinates a `doc_id` not in `available_documents`**: executor drops the hallucinated UUID silently before the SQL query; retrieval proceeds with the remaining valid IDs (or falls back to no-filter if all IDs are invalid).
- **User asks a non-comparison document question with two docs uploaded**: planner issues a single `document_retrieval` step without `doc_ids`, normal cosine search across both docs runs.
- **`doc_filename` missing from chunk metadata** (legacy chunks ingested before this change): synthesizer falls back to the first 8 characters of `document_id` as the doc label.
- **Document ingested but `doc_type`/`ticker`/`filing_date` are null** (user uploaded a generic PDF): `doc_filename` is always present; the missing fields are omitted from the label, not surfaced as errors.

## Detailed requirements

### Retrieval layer
1. `DocumentRetrievalInput` gains a new optional field: `doc_ids: list[uuid.UUID] | None = None`. When provided, the list must contain between 1 and 10 UUIDs (validated by pydantic `Field(None, max_length=10)`).
2. `RetrievalService.retrieve()` gains a new parameter `doc_ids: list[uuid.UUID] | None = None`. When set, the SQL query adds `AND document_chunks.document_id IN (:doc_ids)` before the cosine-distance ordering.
3. `RetrievalService.retrieve()` joins the `documents` table on `document_chunks.document_id = documents.id` and adds `doc_filename`, `doc_ticker`, `doc_type`, and `doc_filing_date` (as ISO-8601 string or `null`) to `ChunkResult.metadata`. These four keys are set on every returned chunk; they do not overwrite pre-existing metadata keys of the same name.
4. `doc_ids` values are not pre-validated against conversation ownership in the service layer. Instead, the SQL join naturally returns zero rows for any UUID not belonging to `(user_id, conversation_id)`.
5. When `doc_ids` is `None` or empty, behavior is identical to today (no additional SQL filter).
6. `DocumentRetrievalTool.__call__` passes `doc_ids` through to `retrieval_service.retrieve()`.

### State
7. `AgentState` gains `available_documents: list[dict]`. Each dict has exactly these string keys: `id` (UUID as string), `filename`, `doc_type`, `ticker` (or `null`), `filing_date` (ISO-8601 date string or `null`).
8. `chat.py` populates `available_documents` in the same `async with AsyncSessionFactory()` block that already computes `has_uploaded_documents`. Query: `SELECT id, filename, doc_type, ticker, filing_date FROM documents WHERE conversation_id = ? AND user_id = ? AND status = 'ready' ORDER BY created_at ASC`.
9. `available_documents` is capped at 20 entries (the first 20 by `created_at`) to bound planner prompt growth.
10. When no documents are ready, `available_documents` is an empty list `[]`.

### Planner
11. When `has_docs` is true, the planner's user message includes an `[available_documents: ...]` block after `[has_documents: true]`, containing a compact JSON array of the document dicts.
12. The planner system prompt is updated to document that `document_retrieval` accepts an optional `"doc_ids": ["<uuid>"]` field and to give an example: when the user asks to compare two specific documents, issue one step per document, each with the matching `doc_ids`.
13. The planner's existing 6-step cap applies; a two-doc comparison uses 2 of those steps.
14. The planner must not include `user_id` or `conversation_id` in `doc_ids` calls — the executor injects them as today.

### Tool selector
15. Same `[available_documents: ...]` injection as the planner when `has_documents: true`.
16. The tool selector system prompt is updated to note that `document_retrieval` accepts `doc_ids` for comparison queries but that the tool selector only selects one tool call at a time — if two targeted retrievals are needed the query must be classified as `complex` (handled by the planner, not the tool selector).
17. Tool selector emits a single `document_retrieval` call with `doc_ids` only when the query clearly targets one specific document from the available list.

### Executor
18. When processing a `document_retrieval` step, the executor validates any `doc_ids` values against `state["available_documents"]`. UUIDs not present in `available_documents` are dropped with a `WARNING` log; if all IDs are dropped, the step proceeds with `doc_ids=None` (full-conversation retrieval).
19. `_TOOL_INPUT_MODELS["document_retrieval"]` already uses `DocumentRetrievalInput`; the new `doc_ids` field is picked up automatically once the schema is updated.

### Synthesizer
20. The RAG chunk section changes from `f"[{i}] {chunk['content']}"` to `f"[{i}] [Source: {doc_label}] {chunk['content']}"` where `doc_label` is built from `chunk["metadata"]["doc_filename"]` (truncated to 60 chars). Fallback: first 8 chars of `chunk["metadata"]["document_id"]` if `doc_filename` is absent.
21. `_SYSTEM_PROMPT_RAG` gains rule 6: *"When chunks from multiple distinct source documents are present, structure your answer as a comparison: address each source document separately under a brief label, then provide a summary of the key differences."*
22. Existing citation format `[1]`, `[2]`, etc. is unchanged. The doc label in the chunk line provides the attribution context to the LLM.
23. The comparison rule fires only when two or more distinct `doc_filename` values appear in the chunks — the synthesizer itself does not need to detect this; the instruction is unconditional in the prompt (it degrades gracefully to a single-doc answer when only one source is present).

### Logging and observability
24. `retrieval_service.retrieve()` logs `doc_ids_filter_count: int` (value = `len(doc_ids)` or `0`) in the existing `retrieval_called` event.
25. When a targeted retrieval step (with `doc_ids`) returns zero chunks, the executor logs at WARNING: `executor_retrieval_empty_for_doc_ids` with `doc_ids` and `step_key`.
26. No new metrics or traces beyond structlog events.

### Security
27. `doc_ids` are always scoped by `(user_id, conversation_id)` in the SQL WHERE clause — a user cannot retrieve another user's chunks by passing a foreign `doc_id`.
28. The executor's `available_documents` validation (requirement 18) is an additional defense layer; it prevents the LLM from probing arbitrary UUIDs.

## Data model changes

No new tables. No migrations.

`ChunkResult` (in `backend/app/schemas/document.py`) is not structurally changed. The four attribution fields are stored in the existing `metadata: dict | None` field, which already flows through the pipeline to `ChunkDict.metadata`.

## API contracts

No new endpoints. No changes to HTTP request/response schemas.

`DocumentRetrievalInput` is an internal tool schema not exposed over HTTP.

## Component and file structure

### Backend — modified files
| File | Change |
|---|---|
| `backend/app/schemas/tools/document_retrieval.py` | Add `doc_ids: list[uuid.UUID] \| None = None` to `DocumentRetrievalInput` |
| `backend/app/services/retrieval.py` | Add `doc_ids` parameter; join `Document`; populate attribution metadata in returned `ChunkResult` objects |
| `backend/app/tools/document_retrieval.py` | Pass `input.doc_ids` to `retrieval_service.retrieve()` |
| `backend/app/agent/state.py` | Add `available_documents: list[dict]` to `AgentState` |
| `backend/app/api/v1/chat.py` | Query and populate `available_documents`; pass to `_stream_events`; set in `initial_state` |
| `backend/app/agent/planner.py` | Inject `available_documents` JSON into user message; update system prompt with `doc_ids` example |
| `backend/app/agent/tool_selector.py` | Same injections as planner |
| `backend/app/agent/executor.py` | Validate and strip invalid `doc_ids` before tool call; log empty targeted retrievals |
| `backend/app/agent/synthesizer.py` | Update chunk line format; add comparison rule to `_SYSTEM_PROMPT_RAG` |

### Tests — new files
| File | Purpose |
|---|---|
| `backend/tests/services/test_retrieval_doc_ids.py` | Unit tests for `doc_ids` filter isolation and attribution metadata |
| `backend/tests/agent/test_synthesizer_doc_labels.py` | Unit tests for doc label formatting and fallback behavior |

### Tests — modified files
| File | Change |
|---|---|
| `backend/tests/tools/test_document_retrieval.py` | Add cases for `doc_ids` pass-through |
| `backend/tests/agent/test_planner.py` | Add cases for two-step comparison plan with `doc_ids` |
| `backend/tests/agent/test_executor.py` | Add cases for `doc_ids` validation / drop of invalid UUIDs |

## External dependencies

None. No new third-party libraries or services.

## Testing plan

### Unit tests

**`test_retrieval_doc_ids.py`**
- `retrieve(doc_ids=[doc_a_id])` returns only chunks with `document_id == doc_a_id`, even when doc_b chunks are in the DB
- `retrieve(doc_ids=[unknown_uuid])` returns `[]` without raising
- `retrieve(doc_ids=None)` returns chunks from all documents (existing behavior, regression check)
- Returned `ChunkResult.metadata` contains `doc_filename`, `doc_ticker`, `doc_type`, `doc_filing_date`
- `doc_filename` matches the `Document.filename` of the source row
- When `Document.ticker` is null, `metadata["doc_ticker"]` is `null`, not absent

**`test_synthesizer_doc_labels.py`**
- Chunk with `metadata={"doc_filename": "apple_q2_2024.pdf", ...}` renders as `[1] [Source: apple_q2_2024.pdf]`
- Chunk with `metadata={"document_id": "a1b2c3d4-..."}` (no `doc_filename`) renders as `[1] [Source: a1b2c3d]`
- `doc_filename` longer than 60 chars is truncated to 60 chars in the label
- `_SYSTEM_PROMPT_RAG` contains the comparison rule string

**`test_planner.py` additions**
- For a query like "compare Q2 and Q3" with two docs in `available_documents`, the planner emits exactly two steps, both `document_retrieval`, each with a different `doc_ids` value
- For a single-document query with two docs available, the planner emits one retrieval step without `doc_ids`

**`test_executor.py` additions**
- UUID in `doc_ids` that is not in `state["available_documents"]` is dropped before tool call
- All IDs dropped → tool called with `doc_ids=None`
- Zero-chunk result from targeted retrieval logs `executor_retrieval_empty_for_doc_ids`

### Integration tests

- Two documents uploaded, comparison query → two `document_retrieval` tool calls in SSE stream, each attributed → synthesizer output contains distinct source labels from both documents
- Single document uploaded, single-doc query → one tool call, no `doc_ids` filter, no regression
- Two documents uploaded, non-comparison question → one tool call, no `doc_ids` filter

### Manual verification steps
1. Upload `apple_q2_2024.pdf` and `apple_q3_2024.pdf` to a conversation.
2. Ask *"How did Apple's revenue guidance change between Q2 and Q3?"*
3. Confirm SSE stream emits exactly two `tool_call` events for `document_retrieval`.
4. Confirm the assistant response contains phrases that reference both documents by name or period.
5. Ask a single-document question (*"What does the Q2 filing say about iPhone sales?"*) and confirm the response is coherent and correctly attributed without regression.
6. Upload a single document and ask a comparison question — confirm graceful degradation (single-doc response, no error).

## Observability

**Healthy state**
- Two `tool_call` SSE events for `document_retrieval` with `status: "complete"` for a comparison query
- `retrieval_called` log has `doc_ids_filter_count: 1` for each targeted call
- Synthesizer `reranked_chunks` contain distinct `doc_filename` values from both documents

**Unhealthy state**
- `executor_retrieval_empty_for_doc_ids` warning for one or both targeted calls — indicates the planner matched the wrong UUID or the document was deleted mid-turn
- `planner_invalid_tool` or `tool_selector_input_invalid` — indicates LLM produced a malformed `doc_ids` value

## Risks and open questions

**Planner doc-matching depends on filename quality** — the planner infers which document corresponds to "Q2" from the filename. If the user uploads files named `scan001.pdf` and `scan002.pdf`, the LLM has no signals to distinguish them. This is noted in out-of-scope (automatic pairing is excluded); users are responsible for meaningful filenames. Risk: medium. No mitigation in this spec.

**Cross-encoder reranking is per-step, not cross-doc** — each targeted retrieval independently reranks its own candidates, so the cross-encoder cannot inadvertently suppress one document in favor of another. This is the correct behavior and a benefit of the per-step isolation approach.

**`available_documents` freshness** — the list is loaded once at request start. If a document finishes ingesting mid-turn (unlikely in practice given the Phase 1 polling gate), it will not appear in `available_documents` for the current turn. Acceptable given the polling gate already ensures all just-uploaded docs are ready before the agent runs.

**Token budget for `available_documents` in planner prompt** — each document adds ~100 tokens. Capped at 20 documents = ~2,000 additional tokens. Acceptable for GPT-4o-class models used for planning; revisit if moving to smaller models.

**Attribution metadata for pre-existing chunks** — chunks ingested before this change have no `doc_filename` in their metadata. The `doc_filename` field is populated at retrieval time by joining the `documents` table (not stored in `chunk_metadata`), so all historical chunks automatically get attribution after this change is deployed. No backfill needed.
