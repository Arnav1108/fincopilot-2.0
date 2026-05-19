# Spec: Database Schema (Initial)

## Goal
Define and migrate the complete PostgreSQL schema for FinCopilot so that all application features — auth, conversations, document ingestion, vector search, and eval — have a persistent storage foundation.

## Background
The application has no persistent storage today. `backend/app/database.py` defines a SQLAlchemy `Base` and async engine but no models exist. Alembic is installed but not initialized. No tables exist in the database. `pgvector/pgvector:pg15` is the Postgres image used in Docker Compose, but the `vector` extension must be explicitly enabled per database. This spec covers the full initial schema — greenfield, no existing data to migrate.

## Scope

### In scope
- Alembic initialization with async SQLAlchemy support
- 7 SQLAlchemy model files: `users`, `analyst_profiles`, `conversations`, `messages`, `documents`, `document_chunks`, `eval_runs`
- One hand-written Alembic migration that enables pgvector and creates all tables, indexes, and constraints in the correct dependency order
- HNSW index on `document_chunks.embedding`
- Composite index on `document_chunks(user_id, document_id)`
- UUID primary keys on all tables
- PostgreSQL native enum types for `messages.role`, `documents.doc_type`, and `documents.status`

### Out of scope
- Seed data of any kind
- Admin UI or any frontend changes
- Document ingestion pipeline (Phase 3)
- Row-level security at the PostgreSQL level (enforced in the application layer)
- Any financial data tables not listed above
- Alembic autogenerate from models (migration is written manually for precision)
- Celery task tables or beat schedule tables

## User flow
This feature has no interactive user flow — it is infrastructure. The relevant operational flows are:

**Fresh deployment:**
1. Developer runs `docker compose up postgres` (or `docker compose up --build`)
2. Developer activates the Python venv and runs `alembic upgrade head` inside `backend/`
3. Alembic connects to Postgres, enables the `vector` extension, and creates all 7 tables with indexes
4. Application starts and can immediately read/write all tables

**Subsequent deploys:**
1. New migrations (future) are appended; `alembic upgrade head` applies only the delta
2. Rollback with `alembic downgrade -1` reverses the last migration

## Detailed requirements

1. All primary keys are `UUID` generated with `gen_random_uuid()` (Postgres built-in), not serial integers.
2. Alembic must be initialized inside `backend/` using the async `asyncio` backend so it shares the same `asyncpg` engine used by the application.
3. `alembic.ini` must read the database URL from the `DATABASE_URL` env var (not hardcoded), falling back to the same default as `app/config.py`.
4. The first migration must begin with `CREATE EXTENSION IF NOT EXISTS vector` before any table DDL.
5. `users.clerk_user_id` must have a `UNIQUE` constraint and a `NOT NULL` constraint. It is the primary lookup key used by `clerk_auth()`.
6. `users.email` and `users.display_name` are nullable (no PII storage requirement).
7. `users.is_active` defaults to `true`.
8. `analyst_profiles` has a one-to-one foreign key to `users.id` with `ON DELETE CASCADE`. A `UNIQUE` constraint on `user_id` enforces the one-to-one relationship.
9. `analyst_profiles.sectors_of_interest` is stored as `TEXT[]` (PostgreSQL array).
10. `conversations.rolling_summary` is `TEXT`, nullable, capable of holding up to ~2000 tokens (~8000 characters) without truncation.
11. `messages.role` is a PostgreSQL native enum `message_role` with values `('user', 'assistant')`, `NOT NULL`.
12. `messages.agent_trace` is `JSONB`, nullable, for LangSmith trace data.
13. `messages.token_count` is `INTEGER`, nullable.
14. `documents.doc_type` is a PostgreSQL native enum `document_type` with values `('10-K', '10-Q', 'transcript', 'presentation', 'research_note', 'other')`, `NOT NULL`.
15. `documents.status` is a PostgreSQL native enum `document_status` with values `('pending', 'processing', 'ready', 'failed')`, defaults to `'pending'`, `NOT NULL`.
16. `documents.error_message` is `TEXT`, nullable, populated only when `status = 'failed'`.
17. `document_chunks.embedding` is `vector(1536)`, nullable (set after embedding is computed), using the pgvector column type.
18. `document_chunks.user_id` is denormalized (copied from the parent document) to avoid a join on every retrieval query.
19. A composite `btree` index on `document_chunks(user_id, document_id)` must exist to support the primary retrieval query pattern.
20. An `HNSW` index on `document_chunks.embedding` using `vector_cosine_ops` with `m=16` and `ef_construction=64` must exist for approximate nearest-neighbor search.
21. `document_chunks.metadata` is `JSONB`, nullable, for section name, page number, etc.
22. `eval_runs` has no foreign key to any user or document — it is a standalone audit table for batch evaluation runs.
23. All `created_at` columns are `TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()`.
24. All `updated_at` columns are `TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()`. The application layer must set `updated_at` explicitly on every `UPDATE` — no trigger is used (out of scope).
25. Every SQLAlchemy model must be importable from `app.models` without error after implementation.
26. `alembic upgrade head` run against a fresh Postgres instance must complete with exit code 0 and no errors.
27. `alembic downgrade base` must cleanly drop all tables and the `vector` extension without error.

## Data model changes

### Table: `users`
```sql
CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_user_id TEXT NOT NULL UNIQUE,
    email         TEXT,
    display_name  TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
**Indexes:**
- Primary key on `id` (implicit)
- `UNIQUE` index on `clerk_user_id` — every authenticated request looks up by this value

### Table: `analyst_profiles`
```sql
CREATE TABLE analyst_profiles (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    preferred_name           TEXT,
    firm                     TEXT,
    role                     TEXT,
    sectors_of_interest      TEXT[],
    preferred_output_length  TEXT,
    preferred_citation_style TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
**Indexes:**
- Primary key on `id` (implicit)
- `UNIQUE` on `user_id` — enforces one-to-one with `users`

### Table: `conversations`
```sql
CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT,
    rolling_summary TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
**Indexes:**
- Primary key on `id` (implicit)
- `btree` index on `user_id` — all conversation list queries filter by user

### Table: `messages`
```sql
CREATE TYPE message_role AS ENUM ('user', 'assistant');

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            message_role NOT NULL,
    content         TEXT NOT NULL,
    token_count     INTEGER,
    agent_trace     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
**Indexes:**
- Primary key on `id` (implicit)
- `btree` index on `conversation_id` — all message reads filter by conversation

### Table: `documents`
```sql
CREATE TYPE document_type AS ENUM (
    '10-K', '10-Q', 'transcript', 'presentation', 'research_note', 'other'
);
CREATE TYPE document_status AS ENUM ('pending', 'processing', 'ready', 'failed');

CREATE TABLE documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    source_url    TEXT,
    doc_type      document_type NOT NULL,
    ticker        TEXT,
    filing_date   DATE,
    status        document_status NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
**Indexes:**
- Primary key on `id` (implicit)
- `btree` index on `user_id` — all document list queries filter by user
- `btree` index on `(user_id, status)` — status-filtered document list queries per user

### Table: `document_chunks`
```sql
CREATE TABLE document_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1536),
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
**Indexes:**
- Primary key on `id` (implicit)
- `btree` composite index on `(user_id, document_id)` — every retrieval query filters on `user_id` first, then `document_id`; user_id leading to enforce ownership before narrowing to document
- `HNSW` index on `embedding vector_cosine_ops` with `m=16, ef_construction=64` — approximate nearest-neighbor search for semantic retrieval

### Table: `eval_runs`
```sql
CREATE TABLE eval_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date                DATE NOT NULL,
    model_version           TEXT NOT NULL,
    faithfulness_score      NUMERIC(5,4),
    answer_relevancy_score  NUMERIC(5,4),
    context_recall_score    NUMERIC(5,4),
    context_precision_score NUMERIC(5,4),
    total_pairs             INTEGER NOT NULL,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
**Indexes:**
- Primary key on `id` (implicit)
- `btree` index on `run_date` — chronological eval history queries

### Migration dependency order
1. Enable `vector` extension
2. Create enum types: `message_role`, `document_type`, `document_status`
3. Create `users`
4. Create `analyst_profiles` (FK → users)
5. Create `conversations` (FK → users)
6. Create `messages` (FK → conversations)
7. Create `documents` (FK → users)
8. Create `document_chunks` (FK → documents, users)
9. Create `eval_runs` (no FKs)
10. Create all secondary indexes (after all tables exist)

## API contracts
No new API endpoints are introduced by this spec. Existing routes in `backend/app/api/router.py` are unchanged. Future specs will add endpoints that consume these models.

## Component and file structure

### Backend — new files
| File | Purpose |
|------|---------|
| `backend/alembic.ini` | Alembic configuration; sqlalchemy.url reads from `DATABASE_URL` env var |
| `backend/alembic/env.py` | Async Alembic env using asyncio runner; imports `Base` metadata |
| `backend/alembic/script.py.mako` | Default migration template (generated by `alembic init`) |
| `backend/alembic/versions/0001_initial_schema.py` | Hand-written migration: enables vector, creates all 7 tables and all indexes |
| `backend/app/models/user.py` | `User` and `AnalystProfile` SQLAlchemy models |
| `backend/app/models/conversation.py` | `Conversation` and `Message` SQLAlchemy models |
| `backend/app/models/document.py` | `Document` and `DocumentChunk` SQLAlchemy models |
| `backend/app/models/eval.py` | `EvalRun` SQLAlchemy model |

### Backend — modified files
| File | Change |
|------|--------|
| `backend/app/models/__init__.py` | Import and re-export all models so `Base.metadata` is fully populated when Alembic's `env.py` imports it |
| `backend/pyproject.toml` | Add `pgvector` package dependency |

### Config — unchanged
`backend/app/config.py` and `backend/app/database.py` require no changes.

## External dependencies
| Dependency | Purpose | If unavailable | Notes |
|-----------|---------|----------------|-------|
| `pgvector` (Python package) | Provides `Vector` SQLAlchemy type | Models fail to import | Must be added to `pyproject.toml` |
| `pgvector/pgvector:pg15` (Docker image) | Postgres with vector extension compiled in | No ANN search | Already in `docker-compose.yml` |
| `asyncpg` | Async Postgres driver | DB connections fail | Already a dependency |
| `alembic` | Migration runner | Cannot create schema | Already installed |

## Implementation plan

### Step 1 — Initialize Alembic with async support
**What to build:** Run `alembic init alembic` inside `backend/`. Rewrite `alembic/env.py` to use the async runner pattern (`asyncio.run`, `run_async_migrations`). Update `alembic.ini` to read the database URL from the `DATABASE_URL` env var with the same default as `config.py`.
**Files changed:** `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`
**Verify:** `cd backend && alembic current` exits 0 with no traceback.

### Step 2 — Write SQLAlchemy models
**What to build:** Create `app/models/user.py` (`User`, `AnalystProfile`), `app/models/conversation.py` (`Conversation`, `Message`), `app/models/document.py` (`Document`, `DocumentChunk`), `app/models/eval.py` (`EvalRun`). Update `app/models/__init__.py` to import all models. Add `pgvector>=0.2.0` to `pyproject.toml`.
**Files changed:** 4 new model files, `backend/app/models/__init__.py`, `backend/pyproject.toml`
**Verify:** `python -c "from app.models import User, AnalystProfile, Conversation, Message, Document, DocumentChunk, EvalRun; print('ok')"` prints `ok` with no errors.

### Step 3 — Write the manual migration
**What to build:** Create `alembic/versions/0001_initial_schema.py` by hand. `upgrade()`: enable vector extension, create 3 enum types, create all 7 tables in dependency order, create all secondary indexes (including HNSW). `downgrade()`: drop indexes, tables, enums, and extension in reverse order.
**Files changed:** `backend/alembic/versions/0001_initial_schema.py`
**Verify:** `python -m py_compile alembic/versions/0001_initial_schema.py` exits 0. `alembic check` shows the migration is pending.

### Step 4 — Apply migration and verify schema
**What to build:** No new code. Apply the migration against Docker Postgres and verify the result.
**Files changed:** None (database state only)
**Verify:**
- `alembic upgrade head` exits 0 on a fresh database
- `alembic current` shows `0001_initial_schema (head)`
- In psql: `\dt` lists all 7 tables; `\d document_chunks` shows `embedding vector(1536)` and the HNSW index
- `SELECT * FROM pg_extension WHERE extname = 'vector';` returns 1 row
- `alembic downgrade base` exits 0 and drops all tables
- `alembic upgrade head` again succeeds (idempotency)

## Testing plan

### Unit tests
- `tests/models/test_imports.py`: import every model class, assert it is a subclass of `Base`, assert `Base.metadata.tables` contains all 7 table names
- `tests/models/test_enums.py`: assert enum values match the spec (e.g., `MessageRole` has exactly `user` and `assistant`)

### Integration tests (require running Postgres)
- `tests/db/test_migration.py`: run `alembic upgrade head`, assert all 7 tables exist in `information_schema.tables`, run `alembic downgrade base`, assert tables are gone
- `tests/db/test_schema.py`: after upgrade, verify `document_chunks.embedding` column type is `USER-DEFINED` (vector); verify HNSW index exists in `pg_indexes`

### Manual verification steps
1. `docker compose up postgres -d`
2. `cd backend && source venv/bin/activate && alembic upgrade head` — must exit 0
3. `psql postgresql://fincopilot:fincopilot@localhost:5432/fincopilot`
4. `\dt` — confirm 7 tables present
5. `\d document_chunks` — confirm `embedding vector(1536)` column and HNSW index
6. `SELECT * FROM pg_extension WHERE extname = 'vector';` — 1 row returned
7. `alembic downgrade base` — exit 0, all tables gone
8. `alembic upgrade head` again — must succeed

## Observability
- Alembic logs each DDL statement at `INFO` level to stdout during migration
- No application-level metrics for schema changes (infrastructure-only feature)
- Healthy state: `alembic current` == `(head)`, all 7 tables present in `information_schema.tables`
- Unhealthy state: `alembic current` shows a partial or diverged head; remediate with `alembic history` then `alembic upgrade head`

## Risks and open questions

1. **Async Alembic env.py complexity**: The async runner requires careful event loop handling. Fallback: use a sync `postgresql+psycopg2://` URL only in `alembic.ini` (adding `psycopg2-binary` as a dev dep) while keeping `asyncpg` for the app. Defer this decision to Step 1.

2. **HNSW index build time**: On large `document_chunks` tables, building HNSW is expensive. For now the index is in the migration. If the table exceeds ~1M rows before indexing, the index should be created `CONCURRENTLY` outside the migration transaction — not a concern for initial deploy.

3. **PostgreSQL enum rigidity**: Values cannot be removed from an enum without dropping and recreating the type. `doc_type` values should be treated as stable; adding new types requires a future migration, not a code change.

4. **`updated_at` staleness**: Without a trigger, any raw SQL `UPDATE` that omits `updated_at` leaves the column stale. A future spec should add a trigger. For now, all ORM-based updates must set it explicitly.

5. **pgvector Python package version**: Pin to `pgvector>=0.2.0` in `pyproject.toml`. Must be compatible with pgvector Postgres 15 and the `vector(1536)` type.

6. **`filing_date` as `DATE`**: Stored without time component — SEC filing dates are calendar dates. If intraday precision is ever needed, a migration to `TIMESTAMPTZ` will be required.
