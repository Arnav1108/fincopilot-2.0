# Spec: Cross-Session Memory

## Goal
Extract key facts from each conversation and inject them into future sessions so the agent builds a persistent understanding of each user's investment interests and research habits across conversations.

## Background
Today, `AgentState.analyst_profile` is hardcoded to `{}` in `chat.py` every time a stream starts. The synthesizer already reads this field and formats it into the context prompt (`for k, v in analyst_profile.items()`), but nothing ever populates it with user-specific knowledge.

Within a conversation, `Conversation.rolling_summary` and `MemoryManager` provide short-term memory. But when a new conversation begins, the agent has no recollection of prior sessions — the user who asked about Tesla three times last week gets the same blank-slate response as a first-time user.

**Prior decisions that constrain this design:**
- `AgentState` values must be JSON-serializable primitives (constraint documented at the top of `state.py`).
- Celery tasks use a synchronous psycopg2 engine (see `tasks/ingestion.py`) — async SQLAlchemy is unavailable inside task functions.
- The Celery include list in `celery_app.py` must be updated for new task modules.
- All new settings must go through `app/config.py` as pydantic-settings fields.

## Scope

### In scope
- New `user_memories` database table storing extracted facts per user
- Celery task (`extract_memories`) that runs after each assistant response, calls GPT-4o-mini, and upserts facts
- 20-fact cap per user; oldest facts are evicted when the limit is reached
- Memory injection into `AgentState.analyst_profile` at the start of every chat stream
- `GET /api/v1/memories` endpoint returning the user's stored memories
- `DELETE /api/v1/memories` endpoint clearing all memories for the user
- Alembic migration for the new table

### Out of scope
- Memory editing by the user (create/update individual facts)
- Per-conversation memory toggle
- Semantic or keyword search over memories
- Sharing memories across users
- Memory versioning or change history
- Surfacing memories in the frontend UI
- Extracting memories from documents (only conversation messages are source material)

## User flow

### Happy path — memory accumulation
1. User sends a message; agent responds normally (no change to this flow).
2. After the assistant message is saved to the DB (Phase 4 in `_stream_events`), `extract_memories.delay(user_id, conversation_id)` is fired — non-blocking, returns immediately.
3. Celery worker picks up the task. It reads the last user + assistant message pair from the conversation.
4. Worker calls GPT-4o-mini with the extraction prompt. The model returns a JSON array of `{fact_type, content}` objects (or an empty array if nothing notable).
5. Worker validates each fact: non-empty content ≤ 200 chars, fact_type in allowed enum.
6. Worker computes how many rows to evict: `max(0, current_count + len(new_facts) - 20)`. Deletes that many oldest rows (ORDER BY `created_at` ASC).
7. Worker bulk-inserts the new facts.

### Happy path — memory injection
1. User sends any message to any conversation.
2. In `chat.py`, after `MemoryManager().load_memory()` completes, a second async DB call fetches all `user_memories` rows for this user (ordered by `created_at` ASC).
3. Facts are grouped by `fact_type` and formatted into a single `prior_context` string (newline-separated bullet points).
4. `analyst_profile` in `AgentState` is set to `{"prior_context": "<formatted facts>"}` (or `{}` if there are no memories yet).
5. The synthesizer includes this in its context prompt under "Analyst profile:", influencing the response naturally.

### Edge cases and error states

| Situation | Behaviour |
|---|---|
| Extraction task: GPT returns invalid JSON | Log `memory_extraction_parse_failed` at WARNING, skip insertion, do not retry |
| Extraction task: GPT returns empty array | Log `memory_extraction_empty`, exit normally — no rows inserted |
| Extraction task: DB error on insert | Celery retries up to 2 times with 10 s backoff; on final failure, log ERROR |
| Memory load fails at session start | Catch exception, log ERROR, inject empty `analyst_profile` — never block the stream |
| User has 0 memories | `analyst_profile = {}` — synthesizer omits the "Analyst profile" section entirely |
| User clears memories mid-conversation | Next message in the same conversation still uses the in-memory state from when it was loaded; following message will load `{}` |
| Conversation deleted | `conversation_id` FK in `user_memories` goes to NULL (ON DELETE SET NULL) — facts are retained |
| User account deleted | Cascade DELETE removes all `user_memories` rows |

## Detailed requirements

1. `extract_memories` is a Celery task registered in `celery_app.py` under `app.tasks.memory_extraction`.
2. The task accepts `user_id: str` and `conversation_id: str` as plain string arguments (JSON-serializable).
3. The task reads exactly the last 2 messages (most recent user + assistant pair) from the given conversation using the synchronous psycopg2 engine pattern established in `tasks/ingestion.py`.
4. The extraction prompt instructs the model to return ONLY a JSON array of objects with keys `fact_type` (string) and `content` (string). If nothing notable is present, it must return `[]`.
5. Allowed `fact_type` values: `ticker_interest`, `sector_interest`, `investment_style`, `research_pattern`. Facts with any other type are discarded with a WARNING log.
6. Each `content` value must be ≤ 200 characters of plain text. Facts exceeding this are truncated to 200 characters before insertion.
7. `content` must not be blank. Blank facts are discarded silently.
8. The extraction prompt must explicitly instruct the model not to include names, email addresses, phone numbers, physical addresses, account numbers, or any other personally identifiable information.
9. Maximum 20 `user_memories` rows per user. Before inserting N new facts, the task deletes the `max(0, current_count + N - 20)` oldest rows for that user (by `created_at` ASC).
10. The task uses GPT-4o-mini (configurable via `settings.MEMORY_EXTRACTION_MODEL`, default `"gpt-4o-mini"`).
11. `USER_MEMORY_MAX_COUNT` is a settings field defaulting to `20`.
12. Memory load at session start is an async DB query added to `MemoryManager` (or a standalone async function called from `chat.py`). It must complete before `AgentState` is constructed.
13. If memory load raises any exception, `analyst_profile` defaults to `{}` and the exception is logged at ERROR level — the stream must not be interrupted.
14. Memories are injected as `{"prior_context": "<bullet list>"}` where each bullet is `- [fact_type] content`. If the user has no memories, inject `{}`.
15. `GET /api/v1/memories` returns all memories for the authenticated user, ordered by `created_at` ASC, with fields `id`, `fact_type`, `content`, `conversation_id`, `created_at`.
16. `DELETE /api/v1/memories` deletes all `user_memories` rows for the authenticated user and returns HTTP 204.
17. Both memory endpoints require a valid Clerk JWT (same `clerk_auth` dependency used everywhere).
18. The task fires after every assistant response, including on RAG, tool, and LLM-only paths — but not on ingest-classification responses (where `final_output` is empty) and not on error responses.
19. The `extract_memories` task must be fire-and-forget from the HTTP request path (`delay()` call returns immediately; `_stream_events` does not await the task result).
20. All new log entries use structlog with `snake_case` event names: `memory_extraction_started`, `memory_extraction_completed`, `memory_extraction_parse_failed`, `memory_extraction_empty`, `memory_extraction_db_error`, `user_memories_loaded`, `memories_cleared`.

## Data model changes

### New table: `user_memories`

```sql
CREATE TABLE user_memories (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fact_type   TEXT        NOT NULL,
    content     TEXT        NOT NULL,
    conversation_id UUID    REFERENCES conversations(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Columns:**

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID | NOT NULL | PK, auto-generated |
| `user_id` | UUID | NOT NULL | FK → `users.id` CASCADE DELETE |
| `fact_type` | TEXT | NOT NULL | One of `ticker_interest`, `sector_interest`, `investment_style`, `research_pattern` |
| `content` | TEXT | NOT NULL | Plain text, ≤ 200 chars |
| `conversation_id` | UUID | NULL | FK → `conversations.id` SET NULL — retained when conversation is deleted |
| `created_at` | TIMESTAMPTZ | NOT NULL | Insertion time; used for eviction ordering |

**Indexes:**

```sql
CREATE INDEX ix_user_memories_user_id_created_at
    ON user_memories (user_id, created_at);

CREATE INDEX ix_user_memories_conversation_id
    ON user_memories (conversation_id)
    WHERE conversation_id IS NOT NULL;
```

- `(user_id, created_at)`: covers all three access patterns — load all for user (ORDER BY created_at), eviction (ORDER BY created_at ASC LIMIT n), and count per user.
- `conversation_id` (partial): covers the FK constraint check and any future "what was remembered from conversation X" query; partial index skips the NULLs.

**No changes to existing tables.**

**Migration order:** single migration, no dependencies on other pending migrations.

### SQLAlchemy model (`backend/app/models/memory.py`)

```python
class UserMemory(Base):
    __tablename__ = "user_memories"

    id: Mapped[uuid.UUID]  # PK
    user_id: Mapped[uuid.UUID]  # FK users.id CASCADE DELETE, indexed
    fact_type: Mapped[str]  # TEXT NOT NULL
    content: Mapped[str]   # TEXT NOT NULL
    conversation_id: Mapped[Optional[uuid.UUID]]  # FK conversations.id SET NULL
    created_at: Mapped[datetime]  # TIMESTAMPTZ NOT NULL DEFAULT now()
```

## API contracts

### GET /api/v1/memories

**Purpose:** Return all stored memories for the authenticated user.

- **Auth:** Clerk JWT required (`clerk_auth` dependency)
- **Request headers:** `Authorization: Bearer <jwt>`
- **Request body:** none
- **Response 200:**
  ```json
  {
    "memories": [
      {
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "fact_type": "ticker_interest",
        "content": "Frequently researches TSLA earnings and price targets",
        "conversation_id": "7c3b1d2e-...",
        "created_at": "2026-05-20T14:32:00Z"
      }
    ],
    "count": 1
  }
  ```
- **Response 401:** `{"detail": "Not authenticated"}` — Clerk JWT missing or invalid
- **HTTP status codes:** 200 (ok), 401 (unauthenticated)
- **Rate limiting:** none (reads from DB, low cost)
- **Ordering:** `created_at` ASC (oldest first)

### DELETE /api/v1/memories

**Purpose:** Clear all stored memories for the authenticated user.

- **Auth:** Clerk JWT required
- **Request headers:** `Authorization: Bearer <jwt>`
- **Request body:** none
- **Response 204:** no body
- **Response 401:** `{"detail": "Not authenticated"}`
- **HTTP status codes:** 204 (deleted), 401 (unauthenticated)
- **Note:** Idempotent — if the user has no memories, returns 204 with no error.

## Component and file structure

### Backend — new files

| File | Purpose |
|---|---|
| `backend/app/models/memory.py` | `UserMemory` SQLAlchemy model and `MemoryFactType` string constants |
| `backend/app/tasks/memory_extraction.py` | `extract_memories` Celery task — reads messages, calls GPT-4o-mini, evicts+inserts facts |
| `backend/app/api/v1/memories.py` | `GET /memories` and `DELETE /memories` endpoint handlers |
| `backend/app/schemas/memory.py` | `MemoryRead` and `MemoryListResponse` Pydantic schemas |
| `backend/alembic/versions/xxxx_add_user_memories.py` | Alembic migration creating the `user_memories` table and its indexes |

### Backend — modified files

| File | Change |
|---|---|
| `backend/app/agent/state.py` | Add `load_user_memories(db, user_id) → list[dict]` async method to `MemoryManager`; returns list of `{fact_type, content}` dicts |
| `backend/app/api/v1/chat.py` | (1) After `MemoryManager().load_memory()`, call `load_user_memories()` and set `analyst_profile`. (2) After Phase 4 (assistant message saved), call `extract_memories.delay(...)` when `final_output` is non-empty |
| `backend/app/celery_app.py` | Add `"app.tasks.memory_extraction"` to the `include` list |
| `backend/app/api/v1/router.py` | Register `memories.router` with prefix `"/memories"` and tag `"memories"` |
| `backend/app/config.py` | Add `MEMORY_EXTRACTION_MODEL: str = "gpt-4o-mini"` and `USER_MEMORY_MAX_COUNT: int = 20` |
| `backend/app/models/__init__.py` | Import `UserMemory` so Alembic autogenerate detects the model |

### Tests — new files

| File | Purpose |
|---|---|
| `backend/tests/test_memory_extraction.py` | Unit tests for the extraction task |
| `backend/tests/test_memories_api.py` | Integration tests for GET/DELETE endpoints |

## External dependencies

| Dependency | Role | Unavailability impact | Rate limits |
|---|---|---|---|
| OpenAI API (GPT-4o-mini) | Extraction inference | Celery task fails, retries 2×, then logs ERROR and drops silently — no impact on the user-facing stream | Shared with other agents; gpt-4o-mini has high rate limits, one call per conversation |
| Redis (Celery broker) | Task queuing | If Redis is down, `delay()` raises; this is caught in a try/except in `_stream_events` so the stream is not broken | N/A |
| PostgreSQL | Memory storage | Load failure → empty profile; extraction failure → Celery retry | N/A |

## Testing plan

### Unit tests (`test_memory_extraction.py`)

- **Extraction parsing:** mock OpenAI response returning a valid JSON array → verify correct rows are constructed
- **Invalid JSON from model:** mock response with non-JSON content → verify task exits without raising, logs `memory_extraction_parse_failed`
- **Empty array from model:** mock `[]` response → verify no DB inserts, logs `memory_extraction_empty`
- **Unknown fact_type:** model returns `fact_type: "unknown_type"` → verify fact is discarded
- **Eviction logic:** user has 20 memories, 3 new facts extracted → verify 3 oldest are deleted before 3 new ones are inserted (total stays at 20)
- **Content truncation:** fact content > 200 chars → verify stored content is exactly 200 chars
- **PII check (manual inspection):** verify extraction prompt contains explicit PII exclusion instruction

### Unit tests (`test_memories_api.py`)

- **GET with no memories:** authenticated user with empty `user_memories` → returns `{"memories": [], "count": 0}`
- **GET with memories:** user has 3 memories → returns all 3 ordered by `created_at` ASC
- **GET unauthenticated:** no Authorization header → 401
- **DELETE with memories:** user has 5 memories → 204, all rows deleted
- **DELETE idempotent:** user has 0 memories → 204 (no error)
- **DELETE unauthenticated:** no Authorization header → 401

### Integration tests

- **End-to-end memory accumulation:** send a message about TSLA in conversation A → wait for Celery task → verify a `ticker_interest` row exists in `user_memories` for the user
- **End-to-end injection:** given a user with existing memories → start a new chat stream → verify `analyst_profile` in the constructed `AgentState` contains `prior_context` with those facts
- **Isolation:** memories from user A are not visible to user B

### Manual verification

1. Send three conversations mentioning Tesla. After each, query `GET /api/v1/memories` and confirm a new fact about TSLA appears.
2. Start a fourth conversation. Ask a vague financial question. Confirm the agent's response acknowledges prior Tesla interest (check the synthesizer's user message in logs).
3. Call `DELETE /api/v1/memories`. Confirm `GET /api/v1/memories` returns `count: 0`.
4. Start a fifth conversation. Confirm the agent response shows no Tesla knowledge.

## Observability

### Logs

| Event | Level | Fields |
|---|---|---|
| `memory_extraction_started` | DEBUG | `user_id`, `conversation_id` |
| `memory_extraction_completed` | INFO | `user_id`, `conversation_id`, `facts_extracted`, `facts_evicted` |
| `memory_extraction_parse_failed` | WARNING | `user_id`, `conversation_id`, `raw_response` (truncated to 200 chars) |
| `memory_extraction_empty` | DEBUG | `user_id`, `conversation_id` |
| `memory_extraction_db_error` | ERROR | `user_id`, `conversation_id`, `error`, `retry_count` |
| `user_memories_loaded` | DEBUG | `user_id`, `count` |
| `user_memories_load_failed` | ERROR | `user_id`, `error` |
| `memories_cleared` | INFO | `user_id`, `rows_deleted` |

### Health / healthy state
- Celery task queue length for `memory_extraction` should be near zero (tasks complete in < 5 s).
- `user_memories` table should never have more than `USER_MEMORY_MAX_COUNT` rows per user.
- Unhealthy: growing Celery task backlog on `memory_extraction` queue; repeated `memory_extraction_db_error` log entries.

## Risks and open questions

**Risks:**

1. **Noisy extraction:** GPT-4o-mini may extract trivial or redundant facts (e.g., every time a user asks any question, it creates a `research_pattern` fact). Mitigation: the extraction prompt should emphasise "only extract facts that would meaningfully personalise future responses." Accept some noise at first — the 20-fact cap limits damage.

2. **PII leak:** The model may include names or identifiers despite instructions. There is no automated PII scanner in this spec. Mitigation: the system prompt is explicit; the `content ≤ 200 chars` constraint limits exposure; users can clear memories at any time.

3. **Sync DB in Celery:** The task must use the psycopg2 synchronous engine, not the asyncpg engine used in FastAPI. This pattern is established in `tasks/ingestion.py` and must be replicated exactly — mixing them causes runtime errors.

4. **Alembic state:** `CLAUDE.md` states alembic was not initialised, but recent commits suggest migrations exist. Before writing the migration, verify `backend/alembic/` exists and `alembic.ini` is configured.

5. **Memory staleness:** A fact like "User follows TSLA" extracted six months ago may no longer be relevant. No TTL or staleness mechanism is in scope. The 20-fact cap with oldest-first eviction provides implicit decay over time.

**Open questions:**

- Should extraction be throttled? If a user has a very long conversation with many exchanges, today's design fires one task per `stream_chat` request. That's one OpenAI call per response — probably fine, but worth monitoring at scale.
- Should the `prior_context` string be capped in length before injection into `AgentState`? With 20 facts × 200 chars each, the injected context could be up to 4,000 chars. Acceptable for now but worth revisiting if prompt costs become a concern.
- Should `ticker_interest` facts be deduplicated (i.e., don't store "user asks about TSLA" twice)? Deduplication is not in scope; the 20-fact eviction cycle handles it implicitly over time.
