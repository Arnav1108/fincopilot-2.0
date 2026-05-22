# Spec: Document Status Stream

## Goal
Give authenticated users real-time document ingestion progress via Server-Sent Events so they no longer need to poll `GET /api/v1/documents/{id}` after uploading a PDF.

## Background
The Celery ingestion task (`backend/app/tasks/ingestion.py`) already transitions a `Document` row through `pending → processing → ready | failed`, persisting each change to PostgreSQL. Currently there is no push mechanism — the frontend must poll. Redis is already running and configured in `settings.REDIS_URL`. The SSE pattern (StreamingResponse, `_sse()` helper, async generator) is established in `backend/app/api/v1/chat.py` and must be followed exactly. The `redis==5.1.1` package is already installed; `redis.asyncio` is a subpackage requiring no additional dependency.

## Scope

### In scope
- New `GET /api/v1/documents/{document_id}/status-stream` SSE endpoint
- Redis Pub/Sub publish calls added to the Celery ingestion task at every status transition
- New async Redis pub/sub helper module (`backend/app/services/redis_pubsub.py`)
- Unit and integration tests

### Out of scope
- WebSocket alternative
- Batch status for multiple documents in one connection
- Cancellation of in-progress ingestion
- Retry logic on the SSE client side
- Frontend UI changes (wiring SSE into the upload UI is a separate task)
- Progress percentage within a stage (only stage-level transitions)
- Fan-out to multiple concurrent streams per document

## User flow

**Happy path:**
1. User uploads a PDF via `POST /api/v1/documents/upload`, receives `{"document_id": "<uuid>", "status": "pending"}`.
2. Frontend immediately opens `GET /api/v1/documents/{document_id}/status-stream` with the Clerk JWT.
3. Endpoint verifies ownership. If the document already has a terminal status (`ready` or `failed`), it emits one `status` event and closes. Otherwise it subscribes to Redis and emits the current DB status.
4. As the Celery worker progresses, it publishes events to Redis channel `document.{document_id}.status`.
5. The SSE endpoint forwards each Redis event to the client as a `status` event.
6. When a `ready` or `failed` event arrives, the endpoint emits it and closes the stream.
7. The frontend shows ingestion complete (or an error).

**Edge cases and error states:**
- **Document already terminal on open**: emit one `status` event from DB and close immediately.
- **Document not found or owned by another user**: return `404` before the stream opens.
- **Unauthenticated request**: return `401` before the stream opens.
- **No event for 60 seconds** (task slow or retrying): emit a `ping` event (`data: {}`) and continue waiting.
- **5-minute total duration reached**: emit `timeout` event and close.
- **Client disconnects early**: generator exits, Redis connection and DB session are cleaned up in `finally`.
- **Redis unavailable**: return `503` before the stream opens.

## Detailed requirements

1. The endpoint path is `GET /api/v1/documents/{document_id}/status-stream`.
2. Auth is required: the `clerk_auth` FastAPI dependency must be applied.
3. If the document does not exist or `document.user_id != authenticated user id`, return `404` before opening the stream.
4. The ingestion task must publish a JSON message to Redis Pub/Sub channel `document.{document_id}.status` immediately after each DB commit that changes status:
   - After `status = processing` is committed (task start)
   - After `status = failed` is committed due to insufficient text
   - After `status = ready` is committed (task success)
   - After `status = failed` is committed due to embedding API error (after exhausting retries)
   - After `status = failed` is committed in the general exception handler
5. Published messages must be JSON-encoded with schema `{"status": string, "chunk_count": int|null, "error_message": string|null}`. `chunk_count` is non-null only on `ready`; `error_message` is non-null only on `failed`.
6. The task must use a synchronous `redis.Redis` client (not `redis.asyncio`) because the task body is synchronous.
7. The SSE generator must subscribe to the Redis channel **before** querying the DB for current status, to avoid missing events published between the DB check and subscription.
8. After subscribing, the generator must query the DB for the current document status.
9. If the current DB status is `ready` or `failed`, the generator must emit one `status` event from DB data and return (close stream), ignoring any buffered Redis messages.
10. If the current DB status is `pending` or `processing`, the generator must emit one `status` event reflecting current DB state, then wait for Redis messages.
11. For each Redis Pub/Sub message received, the generator must emit a `status` event and, if the status is `ready` or `failed`, close the stream immediately after.
12. If no message is received within 60 seconds, the generator must emit a `ping` event (`data: {}`) and continue waiting. The 60-second window resets after each emitted event.
13. The stream must close after a total of 300 seconds regardless of state, emitting a final `timeout` event before closing.
14. The SSE response must use `media_type="text/event-stream; charset=utf-8"` with headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`, matching the chat stream pattern.
15. All SSE frames must use the format `event: {type}\ndata: {json}\n\n`, using the existing `_sse()` helper from `chat.py` (or a shared copy).
16. The generator must clean up the Redis connection in a `try/finally` block so client disconnects do not leak connections.
17. The endpoint must log at INFO: stream opened (document_id, user_id, current_status) and stream closed (document_id, reason: `terminal_event | timeout | client_disconnect`).
18. Each emitted non-ping event must be logged at DEBUG with document_id, event type, and status.

## Data model changes

None. No new tables or migrations are required. All document state already exists in the `documents` table (`status`, `chunk_count`, `error_message`). Redis Pub/Sub carries transient events only; nothing is persisted to Redis.

## API contracts

### `GET /api/v1/documents/{document_id}/status-stream`

**Auth required**: Yes — Clerk JWT in `Authorization: Bearer <token>` header, validated by `clerk_auth` dependency.

**Path parameter**:
| name | type | validation |
|------|------|------------|
| `document_id` | UUID | valid UUID; must exist and belong to the authenticated user |

**Request headers**:
| header | required | notes |
|--------|----------|-------|
| `Authorization` | yes | `Bearer <clerk_jwt>` |
| `Accept` | no | `text/event-stream` (optional; for client clarity) |

**Response — success `200 OK`**:
- `Content-Type: text/event-stream; charset=utf-8`
- `Cache-Control: no-cache`
- `X-Accel-Buffering: no`

SSE event stream. Events emitted in order:

| `event` field | `data` schema | when emitted |
|---------------|---------------|--------------|
| `status` | `{"status": "pending"\|"processing"\|"ready"\|"failed", "chunk_count": int\|null, "error_message": str\|null}` | On stream open (current DB state); and on each Redis transition |
| `ping` | `{}` | Every 60 s with no preceding event |
| `timeout` | `{"message": "stream closed after maximum duration"}` | After 300 s total |

Stream closes (connection ends) after emitting a `status` event with `status: ready` or `status: failed`, or after emitting `timeout`.

**Error responses** (before stream opens):
| status | condition |
|--------|-----------|
| `401 Unauthorized` | Missing or invalid Clerk JWT |
| `404 Not Found` | Document does not exist or belongs to another user |
| `503 Service Unavailable` | Redis connection unavailable |

## Component and file structure

### Backend — new files
- `backend/app/services/redis_pubsub.py`: Async Redis pub/sub helper. Exposes `get_async_redis()` (returns a `redis.asyncio.Redis` instance from `settings.REDIS_URL`) and an async context manager `subscribe(channel)` that yields a `PubSub` object and ensures cleanup on exit.

### Backend — modified files
- `backend/app/tasks/ingestion.py`: Add a module-level sync `redis.Redis` client. After each `db.commit()` that changes document status, call `_publish_status(document_id, status, chunk_count, error_message)` — a private helper that serializes to JSON and calls `redis_client.publish(channel, payload)`. Errors from publish must be caught and logged without failing the task.
- `backend/app/api/v1/documents.py`: Add `GET /{document_id}/status-stream` route and `_status_stream_events` async generator. The route verifies ownership, then returns `StreamingResponse` wrapping the generator.

### Tests — new files
- `backend/tests/test_document_status_stream.py`: Unit and integration tests described in the testing plan.

## External dependencies

| dependency | version | purpose | if unavailable |
|------------|---------|---------|----------------|
| `redis` | 5.1.1 (already installed) | Sync publish in Celery task; async pub/sub in SSE endpoint via `redis.asyncio` subpackage | Task publishes are fire-and-forget (caught error, logged); SSE endpoint returns 503 |

No new packages need to be added to `requirements.txt`.

## Testing plan

### Unit tests
- `test_task_publishes_processing`: mock `redis.Redis.publish`; run a portion of the ingestion task up to the `processing` commit; assert `publish` was called with channel `document.{id}.status` and payload `{"status": "processing", "chunk_count": null, "error_message": null}`.
- `test_task_publishes_ready`: assert publish called with `{"status": "ready", "chunk_count": N, "error_message": null}` after successful task completion.
- `test_task_publishes_failed_no_text`: assert publish called with `{"status": "failed", "chunk_count": null, "error_message": "no extractable text"}` on the insufficient-text branch.
- `test_task_publishes_failed_exception`: assert publish called with `{"status": "failed", ...}` in the general exception handler.
- `test_publish_error_does_not_raise`: if `redis.publish` raises `redis.ConnectionError`, the task must not propagate the error.

### Integration tests
- `test_stream_already_ready`: create a document with `status=ready, chunk_count=5`; open stream; assert response contains exactly one `status` event with `status=ready, chunk_count=5`, then connection closes.
- `test_stream_already_failed`: create a document with `status=failed, error_message="no text"`; open stream; assert one `status` event with `status=failed`, then connection closes.
- `test_stream_404_unknown`: request stream for a non-existent UUID; assert `404`.
- `test_stream_404_other_user`: create document owned by `test_user`; request stream authenticated as `test_user_2`; assert `404`.
- `test_stream_unauthenticated`: request stream with no auth; assert `401` (using `anon_client` fixture).
- `test_stream_receives_events`: create document with `status=processing`; mock Redis pubsub to emit one message `{"status": "ready", "chunk_count": 3, "error_message": null}`; assert stream emits `status(processing)` then `status(ready)` then closes.
- `test_stream_ping`: create document with `status=processing`; mock time so keepalive triggers; assert `ping` event is emitted without closing stream.
- `test_stream_timeout`: mock time to advance 301 seconds; assert `timeout` event emitted and stream closes.

### Manual verification steps
1. `docker-compose up --build` — all services healthy.
2. Obtain a Clerk JWT for a test user.
3. Upload a PDF: `curl -X POST http://localhost:8000/api/v1/documents/upload -H "Authorization: Bearer $JWT" -F "file=@sample.pdf"` — note returned `document_id`.
4. Open the stream before ingestion completes (large PDF helps): `curl -N http://localhost:8000/api/v1/documents/$DOC_ID/status-stream -H "Authorization: Bearer $JWT"`.
5. Observe: first event is `event: status` with `status: processing`; second event is `event: status` with `status: ready` and `chunk_count` populated; connection closes automatically.
6. Repeat step 4 after ingestion is already done — stream should emit one `status: ready` event and close immediately.
7. Verify 404: substitute an unknown UUID in step 4 and confirm `404` response.

## Observability

| event | level | fields |
|-------|-------|--------|
| SSE stream opened | INFO | `document_id`, `user_id`, `current_status` |
| SSE status event emitted | DEBUG | `document_id`, `event_type=status`, `status` |
| SSE ping emitted | DEBUG | `document_id`, `event_type=ping` |
| SSE stream closed: terminal event | INFO | `document_id`, `reason=terminal_event`, `final_status` |
| SSE stream closed: timeout | INFO | `document_id`, `reason=timeout` |
| SSE stream closed: client disconnect | INFO | `document_id`, `reason=client_disconnect` |
| Task Redis publish | DEBUG | `document_id`, `channel`, `status` |
| Task Redis publish error | WARNING | `document_id`, `error` |
| Redis connection error (SSE endpoint) | ERROR | `document_id`, `error` |

**Healthy**: streams open and close with `reason=terminal_event` within seconds of task completion; no Redis errors.
**Unhealthy**: streams closing with `reason=timeout`; repeated Redis publish/subscribe errors; 503 responses.

## Risks and open questions

1. **Race condition (mitigated)**: If the task completes between the endpoint's ownership check and the generator's Redis subscribe, the `ready/failed` event would be missed. Mitigated by subscribing to Redis first, then checking DB — if the DB already shows a terminal state at that point, the generator emits from DB and closes without waiting for Redis.

2. **Pub/Sub message loss before any subscriber**: If the SSE stream is opened *after* the task has already completed and the terminal Redis event was published before subscription, the event is lost. This is safe because the "check DB after subscribe" pattern (requirement 8–9) catches terminal states directly from the database. No event is silently dropped.

3. **Sync Redis client in Celery task**: The task uses synchronous SQLAlchemy; the Redis client must also be synchronous. Do not use `redis.asyncio` in `ingestion.py`. Use a module-level `redis.Redis` instance (matching the pattern of `_engine` and `_SessionLocal` already in the file).

4. **Redis connection per SSE stream**: Each open stream holds one async Redis pub/sub connection for up to 5 minutes. This is acceptable at expected concurrency (single-user financial research tool). No connection pooling needed for pub/sub subscriptions.

5. **`pending` status**: The task never publishes a `pending` event (status is set to `pending` by the upload endpoint). If a stream opens while status is `pending`, the endpoint emits `status(pending)` from DB and waits for the task's `processing` publish. If the task starts very quickly, the client may receive `processing` before the stream emits `pending` — this is harmless, both events are correct.

6. **Celery retry gaps**: During an OpenAI API retry (30 s or 60 s backoff), no Redis event is published. The client will receive a `ping` event at the 60-second keepalive. Retry behavior is considered out of scope; the client should treat `ping` as "still working".

7. **Windows `asyncio` event loop policy**: The test suite already sets `WindowsSelectorEventLoopPolicy` in `conftest.py` for asyncpg compatibility. This policy is also compatible with `redis.asyncio`; no additional configuration needed in tests.
