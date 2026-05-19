# Spec: Conversation CRUD

## Goal
Allow authenticated users to create, list, rename, and delete their own chat conversations so the frontend sidebar can reflect the user's session history.

## Background
The `conversations` table was created in Phase 1 (migration `0001_initial_schema.py`) with columns `id`, `user_id`, `title`, `rolling_summary`, `created_at`, `updated_at`. No backend routes or frontend UI exist for conversations yet. The `rolling_summary` column is reserved for the AI agent (later phase) and is not exposed to clients here. There is no `message_count` column in the actual schema despite earlier references — the table was verified against the migration file. A `deleted_at` column must be added via a new migration to support soft delete.

## Scope

### In scope
- `POST /api/v1/conversations` — create a new conversation
- `GET /api/v1/conversations` — list the authenticated user's non-deleted conversations
- `PATCH /api/v1/conversations/{id}` — rename a conversation title
- `DELETE /api/v1/conversations/{id}` — soft-delete a conversation
- `GET /api/v1/conversations/{id}/messages` — return messages for a conversation (empty array until messages phase)
- Alembic migration adding `deleted_at` (nullable, timestamptz) to `conversations`
- Pydantic request/response schemas
- All queries scoped to `request.state.user_id` — no exceptions

### Out of scope
- Pagination, search, filtering, sorting options
- Conversation sharing, export, tags, folders, archiving
- Bulk delete
- Exposing or writing `rolling_summary`
- Message history migration or message creation
- Admin endpoints
- Hard delete

## User flow

### Happy path
1. User opens the app. Frontend calls `GET /api/v1/conversations` with the user's Clerk JWT. Sidebar renders the returned list ordered most-recent first.
2. User clicks "New chat". Frontend calls `POST /api/v1/conversations`. Backend creates a row with `title = "New Conversation"` and returns the new object. Sidebar prepends it.
3. User double-clicks a conversation title to rename it. Frontend calls `PATCH /api/v1/conversations/{id}` with `{ "title": "Q3 Earnings Analysis" }`. Backend updates `title` and `updated_at`, returns the updated object. Sidebar reflects the new title.
4. User clicks the delete icon on a conversation. Frontend calls `DELETE /api/v1/conversations/{id}`. Backend sets `deleted_at = now()`. Sidebar removes the item. On refresh, `GET /api/v1/conversations` does not return the deleted conversation.
5. User clicks a conversation to open it. Frontend calls `GET /api/v1/conversations/{id}/messages`. Backend returns `[]` (no messages yet).

### Edge cases and error states
- No JWT / invalid JWT → 401 on every endpoint.
- `{id}` exists but belongs to a different user → 404 (never 403 — do not reveal existence).
- `{id}` does not exist → 404.
- `{id}` is already soft-deleted → 404 (treat as non-existent).
- `PATCH` with an empty string title → 422 (validation error; title must be 1–255 characters).
- `PATCH` with a title exceeding 255 characters → 422.
- `PATCH` with no `title` field in the body → 422.
- `DELETE` called twice on the same conversation → second call returns 404.

## Detailed requirements

1. Every SQL query that touches the `conversations` table MUST include `WHERE user_id = :current_user_id`. This applies to SELECT, UPDATE, and soft-delete alike.
2. `POST /api/v1/conversations` MUST create a row with `title = "New Conversation"` and return the created object including its generated UUID.
3. `GET /api/v1/conversations` MUST return only rows where `deleted_at IS NULL` AND `user_id = :current_user_id`, ordered by `updated_at DESC`.
4. `PATCH /api/v1/conversations/{id}` MUST update `title` AND `updated_at = now()` atomically in the same UPDATE statement.
5. `PATCH` title MUST be validated as a non-empty string, maximum 255 characters, after stripping leading/trailing whitespace.
6. `DELETE /api/v1/conversations/{id}` MUST set `deleted_at = now()` and MUST NOT physically remove the row.
7. `GET /api/v1/conversations/{id}/messages` MUST verify the conversation belongs to the current user (applying the same user-scope + deleted_at filter) before querying messages; return 404 if not found.
8. `GET /api/v1/conversations/{id}/messages` MUST return an empty array `[]` when no messages exist — never null.
9. All endpoints MUST return 401 when the Authorization header is missing or the JWT is invalid.
10. All endpoints that accept `{id}` MUST return 404 when the row does not exist, is soft-deleted, or belongs to another user. The response body MUST NOT distinguish between these cases.
11. All datetime fields returned to the client MUST be UTC ISO 8601 strings (e.g., `"2025-05-18T10:23:00Z"`).
12. The new `deleted_at` column MUST be added via an Alembic migration — not by editing the initial migration.
13. The migration MUST be reversible (downgrade removes the column).

## Data model changes

### Migration: `0002_add_conversations_deleted_at.py`

```
ALTER TABLE conversations
  ADD COLUMN deleted_at TIMESTAMPTZ NULL DEFAULT NULL;

CREATE INDEX ix_conversations_deleted_at
  ON conversations (deleted_at)
  WHERE deleted_at IS NULL;
```

**Upgrade:** adds `deleted_at TIMESTAMPTZ NULL` and a partial index covering only non-deleted rows (the common query path).
**Downgrade:** drops the index, then drops the column.

### Updated `conversations` table (after migration)

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| id | UUID | NOT NULL | gen_random_uuid() | PK |
| user_id | UUID | NOT NULL | — | FK → users(id) ON DELETE CASCADE; indexed as `ix_conversations_user_id` |
| title | Text | NULL | — | NULL only in legacy rows; new rows always get "New Conversation" |
| rolling_summary | Text | NULL | — | Reserved for AI agent; not exposed |
| created_at | TimestampTZ | NOT NULL | now() | Set on insert |
| updated_at | TimestampTZ | NOT NULL | now() | Updated on every PATCH |
| deleted_at | TimestampTZ | NULL | NULL | Non-null means soft-deleted |

### SQLAlchemy model change (`backend/app/models/conversation.py`)
Add `deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)`.

## API contracts

### `POST /api/v1/conversations`
- **Auth:** Required (Clerk JWT)
- **Request body:** None (empty body or `{}`)
- **Response 201:**
  ```json
  {
    "id": "uuid",
    "title": "New Conversation",
    "created_at": "2025-05-18T10:23:00Z",
    "updated_at": "2025-05-18T10:23:00Z"
  }
  ```
- **Response 401:** `{ "detail": "Not authenticated" }`

---

### `GET /api/v1/conversations`
- **Auth:** Required (Clerk JWT)
- **Response 200:**
  ```json
  [
    {
      "id": "uuid",
      "title": "Q3 Earnings Analysis",
      "created_at": "2025-05-18T10:00:00Z",
      "updated_at": "2025-05-18T10:23:00Z"
    }
  ]
  ```
  Empty array `[]` when no conversations exist. Ordered by `updated_at DESC`.
- **Response 401:** `{ "detail": "Not authenticated" }`

---

### `PATCH /api/v1/conversations/{id}`
- **Auth:** Required (Clerk JWT)
- **Request body:**
  ```json
  { "title": "New title string" }
  ```
- **Validation:** `title` required, string, 1–255 characters after stripping whitespace.
- **Response 200:**
  ```json
  {
    "id": "uuid",
    "title": "New title string",
    "created_at": "2025-05-18T10:00:00Z",
    "updated_at": "2025-05-18T10:30:00Z"
  }
  ```
- **Response 401:** `{ "detail": "Not authenticated" }`
- **Response 404:** `{ "detail": "Not found" }`
- **Response 422:** Pydantic validation error body

---

### `DELETE /api/v1/conversations/{id}`
- **Auth:** Required (Clerk JWT)
- **Response 204:** No body.
- **Response 401:** `{ "detail": "Not authenticated" }`
- **Response 404:** `{ "detail": "Not found" }`

---

### `GET /api/v1/conversations/{id}/messages`
- **Auth:** Required (Clerk JWT)
- **Response 200:**
  ```json
  []
  ```
  (Empty array in this phase. Schema will be extended when messages are built.)
- **Response 401:** `{ "detail": "Not authenticated" }`
- **Response 404:** `{ "detail": "Not found" }`

## Component and file structure

### Backend

| File | Action | Purpose |
|---|---|---|
| `backend/alembic/versions/0002_add_conversations_deleted_at.py` | Create | Migration adding `deleted_at` column and partial index |
| `backend/app/models/conversation.py` | Modify | Add `deleted_at` mapped column |
| `backend/app/schemas/conversation.py` | Create | Pydantic `ConversationRead`, `ConversationCreate`, `ConversationUpdate` schemas |
| `backend/app/api/conversations.py` | Create | FastAPI router with all 5 endpoints |
| `backend/app/main.py` | Modify | Register the conversations router under `/api/v1` |

### Frontend
None in this phase — spec covers the API layer only.

### Tests

| File | Action | Purpose |
|---|---|---|
| `backend/tests/test_conversations.py` | Create | Integration tests for all endpoints (see Testing plan) |

### Config
No new environment variables required.

## External dependencies
None beyond what already exists (FastAPI, SQLAlchemy async, asyncpg, Clerk JWT validation via `clerk_auth()` dependency).

## Testing plan

### Integration tests (`backend/tests/test_conversations.py`)
All tests use a real test database (no mocks). Each test creates its own user via the test auth fixture.

**POST /api/v1/conversations**
- Creates conversation, returns 201 with id, title="New Conversation", created_at, updated_at.
- Returns 401 with no token.

**GET /api/v1/conversations**
- Returns empty list for new user.
- Returns list of own conversations ordered by updated_at DESC.
- Does not return conversations belonging to a different user.
- Does not return soft-deleted conversations.
- Returns 401 with no token.

**PATCH /api/v1/conversations/{id}**
- Returns 200 with updated title and new updated_at.
- Persists: re-fetching via GET returns the new title.
- Returns 404 for another user's conversation ID.
- Returns 404 for a soft-deleted conversation.
- Returns 404 for a non-existent UUID.
- Returns 422 for empty string title.
- Returns 422 for title exceeding 255 characters.
- Returns 422 for missing title field.
- Returns 401 with no token.

**DELETE /api/v1/conversations/{id}**
- Returns 204.
- Conversation no longer appears in GET /api/v1/conversations after delete.
- Second DELETE on same id returns 404.
- Returns 404 for another user's conversation ID.
- Returns 404 for a non-existent UUID.
- Returns 401 with no token.

**GET /api/v1/conversations/{id}/messages**
- Returns 200 with `[]` for own valid conversation.
- Returns 404 for another user's conversation ID.
- Returns 404 for a soft-deleted conversation.
- Returns 404 for a non-existent UUID.
- Returns 401 with no token.

### Manual verification
1. Start backend. `POST /api/v1/conversations` with a valid JWT → confirm UUID and title in response.
2. `GET /api/v1/conversations` → confirm conversation appears.
3. `PATCH` with new title → re-fetch and confirm title updated.
4. `DELETE` → re-fetch and confirm conversation gone.
5. Use a second user's JWT and try to PATCH/DELETE the first user's conversation ID → confirm 404.
6. `GET /api/v1/conversations/{id}/messages` → confirm `[]`.

## Observability

- Log `INFO` on successful create/delete with `conversation_id` and `user_id`.
- Log `WARNING` when a 404 is returned (helps detect access-pattern anomalies without leaking info).
- No custom metrics in this phase. FastAPI's default request logs capture method, path, status, and latency.

Healthy state: all endpoints return their expected status codes; `GET /api/v1/conversations` latency < 200 ms for a user with up to 500 conversations.

## Risks and open questions

- **`updated_at` not auto-updating at DB level:** The SQLAlchemy model sets `default=now()` but there is no `onupdate` or DB trigger. The PATCH handler must explicitly set `updated_at = datetime.now(UTC)` in the UPDATE statement or via SQLAlchemy's `onupdate` parameter — verify this is wired correctly.
- **Title nullability:** `title` is nullable in the DB schema. Existing rows (if any) may have `NULL` title. The list and detail responses should coerce `NULL` to `"New Conversation"` at the serialization layer to avoid null leaking to clients.
- **`deleted_at` index effectiveness:** The partial index `WHERE deleted_at IS NULL` will be used by the list query but Postgres query planner behavior should be verified with EXPLAIN on a populated test dataset if performance concerns arise later.
- **Message schema deferral:** `GET /api/v1/conversations/{id}/messages` returns a typed empty array now. When messages are built, this endpoint will need a message schema added — the route should be written to make that extension obvious (e.g., `list[MessageRead]` typed as empty).
- **Assumption:** Clerk JWT always contains the user's ID as the `sub` claim and `clerk_auth()` already maps it to `request.state.user_id` — verified from existing auth implementation.
