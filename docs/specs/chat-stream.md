# Spec: Chat Stream

## Goal
Give authenticated users a streaming chat endpoint that proves the SSE wire protocol works end-to-end with realistic agent-phase timing, so the frontend can be built and tested against a real streaming contract before any AI exists.

## Background
Phase 1 delivered conversation CRUD and the messages table. The `GET /api/v1/conversations/{id}/messages` endpoint currently returns an empty array; no chat endpoint exists. The frontend chat UI is being built in parallel and needs a real `text/event-stream` endpoint — not a mock — to develop against. The hardcoded fake response will be replaced by real LangGraph output in Phase 3.

The `messages` table already has all columns needed (`id`, `conversation_id`, `role`, `content`, `token_count`, `agent_trace`, `created_at`). No schema migrations are required for this feature.

## Scope

### In scope
- `POST /api/v1/conversations/{conversation_id}/stream` endpoint using FastAPI `StreamingResponse` with `text/event-stream`
- Save the user message to `messages` before streaming starts
- Emit the six SSE event types in order with the specified delays (see User flow)
- Save the full assembled assistant message to `messages` after the stream completes
- Return 401 when no valid Clerk JWT is present
- Return 404 when `conversation_id` does not exist or belongs to another user
- Return 400 when request body fails validation (missing fields, empty message)
- `MessageRead` Pydantic schema for serializing messages
- Update `GET /{conversation_id}/messages` to return real messages instead of `[]`
- structlog log entries at key points (message saved, stream started, stream completed, errors)

### Out of scope
- Real AI, Claude API, LangGraph, or any model inference
- Tool execution or RAG retrieval
- Message history sent to a model
- Frontend SSE client (next feature)
- Per-user rate limiting
- Client reconnection / `Last-Event-ID` handling
- Retry logic or partial-response recovery
- Streaming token count or live `agent_trace` updates
- Conversation title auto-generation from first message

## User flow

### Happy path
1. User sends `POST /api/v1/conversations/{conversation_id}/stream` with a Bearer token and JSON body `{ "conversation_id": "<uuid>", "message": "<text>" }`.
2. Server validates JWT via `clerk_auth()`. Invalid/missing → 401 before any DB work.
3. Server checks that `conversation_id` exists and belongs to the authenticated user (not soft-deleted). Not found or wrong owner → 404.
4. Server validates request body. Empty or missing `message` → 400.
5. Server saves the user message (`role=user`, `content=message`) to the `messages` table and commits.
6. Server opens the `StreamingResponse` and immediately emits the first event — time-to-first-token must be ≤ 500 ms from request receipt.
7. Server emits the following SSE sequence:

   | # | Event name    | Data payload (JSON)                                              | Delay before this event |
   |---|---------------|------------------------------------------------------------------|-------------------------|
   | 1 | `node_update`  | `{"node": "Routing", "status": "running"}`                      | 0 ms (immediate)        |
   | 2 | `node_update`  | `{"node": "Planning", "status": "running"}`                     | 300 ms after #1         |
   | 3 | `node_update`  | `{"node": "Executing", "status": "running"}`                    | 500 ms after #2         |
   | 4 | `tool_call`    | `{"tool": "search_sec_filings", "input": {"query": "AAPL 10-K 2023"}}` | 0 ms after #3   |
   | 5 | `sources`      | `{"sources": [{"title": "Apple 10-K 2023", "url": "https://sec.gov/fake/aapl-10k-2023"}, {"title": "Apple Q4 Earnings", "url": "https://sec.gov/fake/aapl-q4-2023"}]}` | 200 ms after #4 |
   | 6 | `node_update`  | `{"node": "Synthesizing", "status": "running"}`                 | 100 ms after #5         |
   | 7 | `token`        | `{"token": "<word>"}` × N — canned response emitted word-by-word at ~80 ms intervals, totalling ~2 s | 0 ms after #6 |
   | 8 | `done`         | `{"message_id": "<uuid of saved assistant message>"}`           | 0 ms after last token   |

8. After emitting all tokens, server saves the full assembled assistant message (`role=assistant`, `content=<full canned text>`) to `messages`, commits, then emits the `done` event with the new message's UUID.
9. Stream closes. Client sees `text/event-stream` in DevTools Network tab with events visible in the EventStream panel.

### Edge cases and error states
- **Missing/invalid JWT**: 401 JSON response `{"detail": "Not authenticated"}` before streaming opens.
- **conversation_id not found or wrong owner**: 404 JSON `{"detail": "Not found"}` before streaming opens.
- **Empty message string**: 400 JSON `{"detail": "message must not be blank"}` before streaming opens.
- **Client disconnects mid-stream**: The async generator detects the disconnect (`asyncio.CancelledError`) and stops yielding. The user message row was already committed; the assistant message is not saved (partial response is discarded).
- **DB error saving user message**: 500 before streaming opens; no SSE events emitted.
- **DB error saving assistant message after streaming**: Log the error at `error` level; the `done` event is still emitted but with `"message_id": null` so the client knows persistence failed.

## Detailed requirements

1. The endpoint path is `POST /api/v1/conversations/{conversation_id}/stream`. The `conversation_id` in the path must match the `conversation_id` in the request body; if they differ, return 400.
2. The response `Content-Type` header must be exactly `text/event-stream; charset=utf-8`.
3. The response must include `Cache-Control: no-cache` and `X-Accel-Buffering: no` headers to prevent proxy buffering.
4. Every SSE event must follow RFC 8895 format: `event: <name>\ndata: <json>\n\n`. The `data` field is always a single-line JSON string.
5. Time-to-first-token (first `node_update` event) must be ≤ 500 ms after the HTTP request is received, measured with `curl -w "%{time_starttransfer}"`.
6. The user message must be persisted to the `messages` table (committed) before the first SSE event is emitted.
7. The assistant message must be persisted after all tokens are emitted and before the `done` event is emitted.
8. The `done` event data must include the UUID of the saved assistant message, or `null` if persistence failed.
9. Delays between events must use `asyncio.sleep` — never `time.sleep`.
10. The canned assistant response must be at least 40 words so the token-stream phase lasts a perceptible duration (~2 seconds at 80 ms/word).
11. A request with a missing, expired, or malformed `Authorization: Bearer` token must receive HTTP 401 before any DB read or write.
12. A request referencing a `conversation_id` that does not exist, is soft-deleted (`deleted_at IS NOT NULL`), or belongs to a different user must receive HTTP 404.
13. A request body with a missing `message` field or an empty/whitespace-only `message` value must receive HTTP 400.
14. `GET /api/v1/conversations/{conversation_id}/messages` must return the full list of messages for the conversation in `created_at` ascending order, using the `MessageRead` schema.
15. structlog must emit `chat_stream_started` (info) when streaming begins, `chat_stream_completed` (info) when the `done` event is sent, and `chat_stream_error` (error) on any unhandled exception.
16. No synchronous blocking calls (ORM, sleep, I/O) may be made inside the async generator body.

## Data model changes

No new tables or columns. The existing `messages` table covers all required fields.

### MessageRead schema (new Pydantic model in `backend/app/schemas/conversation.py`)

| Field            | Type           | Nullable | Notes                               |
|------------------|----------------|----------|-------------------------------------|
| `id`             | UUID           | no       |                                     |
| `conversation_id`| UUID           | no       |                                     |
| `role`           | str            | no       | `"user"` or `"assistant"`           |
| `content`        | str            | no       |                                     |
| `created_at`     | datetime (UTC) | no       |                                     |

`token_count` and `agent_trace` are intentionally excluded from the read schema for this phase.

### `conversations.updated_at` touch

When a user message or assistant message is saved, `conversations.updated_at` must be bumped to `NOW()` so the conversation list stays correctly sorted. This is an UPDATE to the `conversations` table inside the same transaction as the message INSERT.

## API contracts

### `POST /api/v1/conversations/{conversation_id}/stream`

**Auth**: required — Clerk JWT in `Authorization: Bearer <token>` header.

**Request headers**:
- `Authorization: Bearer <token>` (required)
- `Content-Type: application/json` (required)

**Path parameter**: `conversation_id` — UUID string.

**Request body**:
```json
{
  "conversation_id": "uuid-string",
  "message": "non-empty string, max 10000 chars"
}
```

Validation rules:
- `conversation_id`: valid UUID, must match path parameter
- `message`: required, non-empty after stripping whitespace, max 10 000 characters

**Success response**: HTTP 200
- `Content-Type: text/event-stream; charset=utf-8`
- `Cache-Control: no-cache`
- `X-Accel-Buffering: no`
- Body: SSE event stream (see User flow table above)

**Error responses**:

| Condition                          | Status | Body                                        |
|------------------------------------|--------|---------------------------------------------|
| Missing/invalid JWT                | 401    | `{"detail": "Not authenticated"}`           |
| conversation not found/wrong owner | 404    | `{"detail": "Not found"}`                   |
| Empty/blank message                | 400    | `{"detail": "message must not be blank"}`   |
| path/body conversation_id mismatch | 400    | `{"detail": "conversation_id mismatch"}`    |
| message > 10 000 chars             | 400    | `{"detail": "message exceeds 10000 chars"}` |
| Internal server error              | 500    | `{"detail": "Internal server error"}`       |

### `GET /api/v1/conversations/{conversation_id}/messages` (modified)

Currently returns `[]`. After this feature it returns the real message list.

**Auth**: required.

**Success response**: HTTP 200, `application/json`
```json
[
  {
    "id": "uuid",
    "conversation_id": "uuid",
    "role": "user",
    "content": "string",
    "created_at": "2026-01-01T00:00:00Z"
  }
]
```
Ordered by `created_at ASC`. Empty array if no messages.

**Error responses**: 401 (no auth), 404 (conversation not found/wrong owner) — same pattern as existing endpoints.

## Component and file structure

### Backend — new files
- `backend/app/api/v1/chat.py` — `POST /{conversation_id}/stream` handler; contains the async SSE generator and the canned event sequence.
- `backend/app/schemas/chat.py` — `ChatRequest` Pydantic model (validates `conversation_id` + `message`).

### Backend — modified files
- `backend/app/schemas/conversation.py` — add `MessageRead` schema.
- `backend/app/api/v1/conversations.py` — update `list_messages` to query and return real `Message` rows using `MessageRead`; import and use `Message` model.
- `backend/app/api/v1/router.py` — register `chat.router` with `prefix="/conversations"` and tag `"chat"`.

### Tests — new files
- `backend/tests/api/test_chat_stream.py` — integration tests (see Testing plan).

### Config — no changes

## External dependencies

None. This feature uses only FastAPI's built-in `StreamingResponse` and `asyncio`. No third-party streaming libraries, no Claude API, no LangGraph.

## Testing plan

### Unit tests (`test_chat_stream.py`)
- `ChatRequest` schema rejects blank message, message over 10 000 chars, mismatched `conversation_id`.
- SSE formatter function produces correctly formatted `event: X\ndata: Y\n\n` strings.

### Integration tests (`test_chat_stream.py`, using httpx `AsyncClient` with `iter_lines`)
- Happy path: authenticated request to a valid conversation returns 200 with `Content-Type: text/event-stream`, emits all 8 event types in order, and leaves two `messages` rows in the DB (user + assistant).
- 401 when `Authorization` header is absent.
- 401 when JWT is expired/invalid.
- 404 when `conversation_id` does not exist.
- 404 when `conversation_id` belongs to a different user.
- 400 when `message` is empty string.
- 400 when `message` is whitespace-only.
- 400 when path `conversation_id` does not match body `conversation_id`.
- After happy path completes, `GET /{conversation_id}/messages` returns 2 messages in correct order.
- `conversations.updated_at` is bumped after the stream completes.

### Manual verification steps
```bash
# 1. Get a JWT from Clerk (copy from browser DevTools after signing in)
TOKEN="<paste token>"
CONV_ID="<create via POST /api/v1/conversations/>"

# 2. Stream the response
curl -N -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"conversation_id\": \"$CONV_ID\", \"message\": \"What is Apple's revenue?\"}" \
     http://localhost:8000/api/v1/conversations/$CONV_ID/stream

# Expected: SSE events appear progressively with visible delays.
# DevTools Network tab must show type "eventsource" and Content-Type text/event-stream.
# EventStream panel shows all 8 event types.

# 3. Verify persistence
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/v1/conversations/$CONV_ID/messages
# Expected: array of 2 messages — user then assistant.

# 4. Verify 401
curl -N -H "Content-Type: application/json" \
     -d "{\"conversation_id\": \"$CONV_ID\", \"message\": \"test\"}" \
     http://localhost:8000/api/v1/conversations/$CONV_ID/stream
# Expected: HTTP 401

# 5. Verify 404
curl -N -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"conversation_id\": \"00000000-0000-0000-0000-000000000000\", \"message\": \"test\"}" \
     http://localhost:8000/api/v1/conversations/00000000-0000-0000-0000-000000000000/stream
# Expected: HTTP 404
```

## Observability

### Log events (structlog, all include `conversation_id` and `user_id` fields)
| Event key                    | Level | When                                                      |
|------------------------------|-------|-----------------------------------------------------------|
| `user_message_saved`         | info  | User message committed to DB                              |
| `chat_stream_started`        | info  | First SSE event about to be yielded                       |
| `chat_stream_completed`      | info  | `done` event emitted; includes `assistant_message_id`     |
| `assistant_message_save_failed` | error | DB error saving assistant message; stream still closes |
| `chat_stream_error`          | error | Unhandled exception in the generator                      |
| `client_disconnected`        | info  | `asyncio.CancelledError` caught mid-stream                |

### Healthy state
- `chat_stream_started` and `chat_stream_completed` appear as a pair in logs for every successful request.
- No `chat_stream_error` entries.
- `messages` table row count increases by 2 per successful stream.

### Unhealthy state
- `chat_stream_error` or `assistant_message_save_failed` in logs.
- `done` events with `"message_id": null` in the stream.
- 5xx responses visible in the FastAPI access log.

## Risks and open questions

1. **Proxy/nginx buffering**: If deployed behind a proxy that buffers `text/event-stream`, the `X-Accel-Buffering: no` header handles nginx; other proxies may need additional configuration. Not a concern for local dev but will need verification pre-deploy.
2. **asyncpg transaction and streaming**: The DB session must be committed and closed before `StreamingResponse` begins yielding — holding an open transaction across the full stream duration ties up a connection pool slot for ~3 seconds per request. The user message commit and the stream must be in separate DB interactions.
3. **Canned response content**: The placeholder text should be finance-flavored so UI developers see realistic output. Decision deferred to implementer; minimum 40 words required.
4. **conversation_id in body vs path**: Including it in the body is redundant given it's in the path. The body field is required by the spec for symmetry with the future real agent payload. The mismatch check adds a small validation burden. Consider dropping it from the body in Phase 3 when the real contract is defined.
5. **`done` event timing**: The assistant message DB write happens inside the stream, which adds latency before `done`. If the DB is slow, the client sees a gap between the last token and `done`. Acceptable for Phase 2; revisit if it causes UI jank.
