# Spec: Agent Stream

## Goal
Replace the hardcoded fake SSE endpoint with a real LangGraph graph invocation that streams live tokens from the Synthesizer node to the frontend chat UI.

---

## Background
Phase 2 built a working SSE client in the frontend that handles six event types (`token`, `node_update`, `tool_call`, `sources`, `done`, `error`). The backend endpoint (`POST /api/v1/chat/{conversation_id}/stream`) was wired to return a canned text response with simulated delays — no real AI was involved.

Phase 3 (agent-graph) delivered a fully compiled LangGraph `StateGraph` in `app/agent/graph.py` with five nodes: `router_node`, `planner_node`, `executor_node`, `evaluator_node`, and `synthesizer_node`. The `synthesizer_node` already calls OpenAI with `stream=True` but immediately collects all tokens into a list and returns `final_output` as a single string, discarding the streaming advantage.

This feature wires the two together: the endpoint invokes the real graph, live tokens stream through an `asyncio.Queue` shared between the synthesizer and the SSE generator, and every node emits structured lifecycle events as it runs.

Prior decisions that constrain the design:
- `AgentState` values must be plain JSON-serializable Python primitives — no `asyncio.Queue` in state.
- The Clerk JWT `clerk_auth` dependency is already in place and must not be bypassed.
- LangSmith settings (`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGCHAIN_TRACING_V2`) already exist in `app/config.py`.
- The `Message` model already has an `agent_trace: Optional[dict]` JSONB column for trace metadata.
- No new Alembic migrations — work within the existing schema.

---

## Scope

### In scope
- Replace `_stream_events` and `CANNED_RESPONSE` in `app/api/v1/chat.py` with real graph invocation.
- Add `model: str` (default `"gpt-4o"`) to `ChatRequest` in `app/schemas/chat.py`.
- Create `app/agent/stream_context.py` with a `ContextVar`-backed event queue and `emit_event` helper.
- Refactor `synthesizer_node` to put each token into the event queue instead of accumulating a list.
- Add `emit_event` calls to all five nodes for `node_update` lifecycle events.
- Add `emit_event` calls to `executor_node` for `tool_call` and `sources` events.
- Load conversation memory (via `MemoryManager.load_memory`) before graph invocation.
- Persist the assistant message to the `messages` table after graph completion.
- Conditionally regenerate rolling summary (at every 6th message) using `MemoryManager.regenerate_summary`.
- Set LangChain/LangSmith OS environment variables at app startup in `main.py`.
- Attach the LangSmith trace URL to the assistant `Message.agent_trace` JSONB column after each run.
- Handle the `ingest` classification path: stream `node_update` for router, then `done` immediately.
- Handle client disconnect gracefully (cancel background task, suppress `asyncio.CancelledError`).
- Unit and integration tests for the new stream plumbing (at minimum: queue wiring, SSE format, ingest path, error path).

### Out of scope
- Frontend changes — the SSE client already handles all six event types.
- Alembic migrations — use existing schema.
- Rate limiting or token budget enforcement — Phase 5.
- RAGAS evaluation harness — Phase 5.
- Redis checkpointer for LangGraph state persistence — in-memory only.
- WebSocket transport — SSE only.
- Real reranker in `executor_node` — the two `TODO` comments stay as-is.
- Per-node LangSmith span customization — set env vars and pass `run_id`, that is sufficient.
- Cancellation acknowledgement sent to the frontend — handle gracefully server-side but no client protocol.
- The document upload endpoint or Celery ingestion pipeline — already built in Phase 3.

---

## User flow

### Happy path (simple or complex query)
1. Frontend sends `POST /api/v1/chat/{conversation_id}/stream` with `{conversation_id, message, model}`.
2. Backend validates JWT, resolves `current_user`.
3. Backend verifies the conversation exists and belongs to the user (404 if not).
4. Backend persists the user's `Message` (role=user) to the DB.
5. Backend calls `MemoryManager.load_memory` to hydrate `conversation_summary` and `recent_messages`.
6. Backend creates an `asyncio.Queue`, sets the stream context ContextVar, and launches the graph as an `asyncio.create_task`.
7. Backend opens the `StreamingResponse` (text/event-stream).
8. **`router_node` starts** → emits `event: node_update data: {"node": "router_node", "status": "running"}` → classifies query.
9. If classification is **simple** or **complex**, graph proceeds to planner (complex) or executor (simple).
10. Each subsequent node emits its own `node_update` as it starts.
11. **`executor_node`** emits `event: tool_call` for each tool step as it starts and completes.
12. After executor completes, emits `event: sources` with the retrieved chunks.
13. **`synthesizer_node`** emits `node_update`, then emits one `event: token` per token delta as they stream from OpenAI. Final `final_output` is assembled from streamed tokens.
14. Graph task completes. Backend persists the assistant `Message` (role=assistant, content=final_output, agent_trace={"langsmith_url": ...}).
15. Backend checks message count; if ≥ 6 total messages and count % 6 == 0, calls `MemoryManager.regenerate_summary` and updates `Conversation.rolling_summary`.
16. Backend emits `event: done data: {"message_id": "<uuid>", "conversation_id": "<uuid>"}`.
17. SSE stream closes.

### Ingest classification path
Steps 1–8 as above. At step 8, `router_node` classifies query as `"ingest"`. `route_after_router` returns `END`. Graph exits after router. No planner, executor, evaluator, or synthesizer runs. Backend detects `state["classification"] == "ingest"`, emits `event: done data: {"message_id": null, "conversation_id": "<uuid>"}` with no assistant message persisted. Frontend uses this signal to trigger the document ingestion flow.

### Error path
Any unhandled exception during graph execution causes the background task to store the exception. SSE generator detects the failure and emits `event: error data: {"message": "<error text>"}`, then closes. If the error occurs before the assistant message is persisted, no assistant message is saved.

### Client disconnect
If the client disconnects mid-stream, the `StreamingResponse` generator raises `GeneratorExit` or the write fails. The background task is cancelled. `asyncio.CancelledError` is suppressed. Any partial message is not persisted.

---

## Detailed requirements

### Functional

1. `POST /api/v1/chat/{conversation_id}/stream` MUST invoke `compiled_graph.ainvoke` with a real `AgentState` populated from the request.
2. The endpoint MUST validate that `body.conversation_id == conversation_id` (path param) and return HTTP 400 if they differ.
3. The endpoint MUST verify the conversation exists and belongs to `current_user.id`; return HTTP 404 if not.
4. `ChatRequest` MUST accept an optional `model` field (string, default `"gpt-4o"`). The value MUST be passed into `initial_state["model"]`.
5. The user's message MUST be persisted as `role=user` before the graph is invoked.
6. `MemoryManager.load_memory` MUST be called before graph invocation; the returned dict MUST be merged into `initial_state`.
7. `initial_state["user_id"]` MUST be `str(current_user.id)`.
8. The graph MUST run as an `asyncio.create_task` (background), concurrent with the SSE generator that reads from the queue.
9. The SSE generator MUST NOT return until the background task has completed and a `done` or `error` event has been emitted.
10. Each node MUST emit `event: node_update data: {"node": "<node_name>", "status": "running"}` at the start of execution, before any I/O.
11. `synthesizer_node` MUST emit one `event: token data: {"token": "<delta>"}` per non-empty token delta from the OpenAI streaming response. No token batching.
12. `executor_node` MUST emit `event: tool_call data: {"tool_name": str, "step_id": str, "status": "running"}` when each plan step starts, and `{"tool_name": str, "step_id": str, "status": "complete"|"error"}` when it finishes.
13. `executor_node` MUST emit `event: sources data: {"chunks": [...]}` after all steps complete, using the normalized, deduplicated `reranked_chunks`. Each chunk MUST have `content`, `metadata`, and `score` fields.
14. On the `ingest` path (graph exits at END after router), the endpoint MUST emit `event: done` with `message_id: null` and NOT persist an assistant message.
15. On any unhandled exception, the endpoint MUST emit `event: error data: {"message": "<str(e)>"}` and close the stream.
16. The assistant `Message` MUST be persisted after graph completion with `role=assistant`, `content=state["final_output"]`.
17. `Message.agent_trace` MUST be set to `{"langsmith_url": "<url>"}` if a LangSmith trace URL is retrievable; otherwise the field is left `null`.
18. After persisting the assistant message, count total messages in the conversation. If `count >= 6` and `count % 6 == 0`, call `MemoryManager.regenerate_summary` and update `Conversation.rolling_summary`.
19. `Conversation.updated_at` MUST be updated to `now()` after the user message is saved.
20. The SSE response MUST include headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`.

### Security

21. All requests MUST be authenticated via the existing `clerk_auth` dependency. Unauthenticated requests return HTTP 403 (Clerk's default).
22. Conversation ownership MUST be verified server-side (user_id match) before any graph invocation or memory load.
23. `body.message` is passed directly into `AgentState.query` — no shell expansion or SQL interpolation occurs.

### LangSmith tracing

24. At app startup (`lifespan` in `main.py`), the following MUST be set in `os.environ`:
    - `LANGCHAIN_API_KEY` = `settings.LANGSMITH_API_KEY`
    - `LANGCHAIN_TRACING_V2` = `"true"` if `settings.LANGCHAIN_TRACING_V2` else `"false"`
    - `LANGCHAIN_PROJECT` = `settings.LANGSMITH_PROJECT`
25. Each graph invocation MUST pass a `RunnableConfig` with a freshly generated `run_id: uuid.UUID` so the trace is addressable after completion.
26. After the graph task completes, retrieve the trace URL via `langsmith.Client().read_run(str(run_id)).url` inside a try/except. On any exception, log at WARNING level and skip — never fail the request due to LangSmith unavailability.

### Performance

27. Tokens from the synthesizer MUST NOT be buffered — each `asyncio.Queue.put_nowait` call happens immediately upon receiving a delta from OpenAI, and the SSE generator MUST `await queue.get()` continuously without additional sleep.
28. The `asyncio.Queue` MUST be created without a maxsize (unbounded) to prevent synthesizer from blocking on a slow consumer.

### Logging/observability

29. Log `chat_stream_started` at INFO with `conversation_id` and `user_id` before launching the background task.
30. Log `user_message_saved` at INFO with `conversation_id` and `user_id` after persisting the user message.
31. Log `graph_invoke_started` at INFO with `conversation_id`, `user_id`, `model`, and `run_id`.
32. Log `chat_stream_completed` at INFO with `conversation_id`, `user_id`, `assistant_message_id`, and `token_count` (len of final_output) after successful persistence.
33. Log `assistant_message_save_failed` at ERROR with `conversation_id` and `user_id` if persistence fails.
34. Log `summary_regenerated` at DEBUG with `conversation_id` and `message_count` when rolling summary is updated.
35. Log `langsmith_trace_url_failed` at WARNING with `run_id` and the error string if trace URL retrieval fails.

---

## Data model changes

No new tables or columns. All required columns already exist:

| Table | Column | Relevant detail |
|---|---|---|
| `messages` | `agent_trace` | `JSONB`, nullable — stores `{"langsmith_url": str}` |
| `conversations` | `rolling_summary` | `Text`, nullable — updated after every 6th message |
| `conversations` | `updated_at` | Updated to `now()` when user message is saved |

No migrations needed.

---

## API contracts

### `POST /api/v1/chat/{conversation_id}/stream`

**Auth required**: Yes — `Authorization: Bearer <clerk_jwt>` header. Clerk validates JWT; missing/invalid returns 403.

**Request path param**: `conversation_id: UUID`

**Request headers**:
```
Content-Type: application/json
Authorization: Bearer <jwt>
```

**Request body**:
```json
{
  "conversation_id": "uuid",
  "message": "string, 1–10000 chars, non-blank",
  "model": "string, optional, default 'gpt-4o'"
}
```

**Validation errors** (HTTP 422): `message` blank, `message` > 10000 chars, malformed UUID.

**Error responses**:
- `400 Bad Request`: `conversation_id` in body differs from path param.
- `404 Not Found`: conversation does not exist or does not belong to authenticated user.
- `403 Forbidden`: missing or invalid JWT (Clerk middleware).

**Success response** (HTTP 200, `text/event-stream; charset=utf-8`):

```
Cache-Control: no-cache
X-Accel-Buffering: no

event: node_update
data: {"node": "router_node", "status": "running"}

event: node_update
data: {"node": "planner_node", "status": "running"}

event: node_update
data: {"node": "executor_node", "status": "running"}

event: tool_call
data: {"tool_name": "document_retrieval", "step_id": "step_0", "status": "running"}

event: tool_call
data: {"tool_name": "document_retrieval", "step_id": "step_0", "status": "complete"}

event: sources
data: {"chunks": [{"content": "...", "metadata": {...}, "score": 0.92}]}

event: node_update
data: {"node": "evaluator_node", "status": "running"}

event: node_update
data: {"node": "synthesizer_node", "status": "running"}

event: token
data: {"token": "Based"}

event: token
data: {"token": " on"}

...

event: done
data: {"message_id": "uuid", "conversation_id": "uuid"}
```

**Ingest path** (`classification == "ingest"`):
```
event: node_update
data: {"node": "router_node", "status": "running"}

event: done
data: {"message_id": null, "conversation_id": "uuid"}
```

**Error event** (emitted instead of `done` on unhandled exception):
```
event: error
data: {"message": "error description"}
```

---

## Component and file structure

### Backend — new files

| File | Purpose |
|---|---|
| `backend/app/agent/stream_context.py` | Defines `_event_queue: ContextVar[asyncio.Queue | None]` and `async def emit_event(event: dict) -> None`. Exported: `set_stream_queue`, `emit_event`. |

### Backend — modified files

| File | Change |
|---|---|
| `backend/app/schemas/chat.py` | Add `model: str = "gpt-4o"` field to `ChatRequest`. |
| `backend/app/api/v1/chat.py` | Replace `CANNED_RESPONSE` and `_stream_events` with real graph invocation using asyncio.Queue. Remove all fake SSE logic. |
| `backend/app/agent/synthesizer.py` | Replace token list accumulation with `await emit_event({"type": "token", "token": delta})` per delta. Still assembles `final_output` from the same deltas for state return. |
| `backend/app/agent/router.py` | Add `await emit_event({"type": "node_update", "node": "router_node", "status": "running"})` at function entry. |
| `backend/app/agent/planner.py` | Add `await emit_event({"type": "node_update", "node": "planner_node", "status": "running"})` at function entry. |
| `backend/app/agent/executor.py` | Add `node_update` emit at entry; add per-step `tool_call` emits (running/complete/error); add `sources` emit after chunks are collected. |
| `backend/app/agent/evaluator.py` | Add `await emit_event({"type": "node_update", "node": "evaluator_node", "status": "running"})` at function entry. |
| `backend/app/main.py` | In the `lifespan` context manager, set `os.environ` for `LANGCHAIN_API_KEY`, `LANGCHAIN_TRACING_V2`, `LANGCHAIN_PROJECT` from `settings` before `yield`. |

### Tests — new files

| File | Purpose |
|---|---|
| `backend/tests/test_agent_stream.py` | Integration + unit tests for the new streaming logic (see Testing plan). |

### Config — no changes
`app/config.py` already has `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGCHAIN_TRACING_V2`. No changes needed.

---

## External dependencies

| Dependency | Purpose | If unavailable |
|---|---|---|
| `openai` (already installed) | Synthesizer streaming from OpenAI | Graph task fails, `error` event emitted |
| `langsmith` (pulled in by `langgraph`) | Retrieve trace URL after graph run | Caught in try/except, `agent_trace` left null, warning logged |
| `langchain-core` | `RunnableConfig` for run_id passthrough | Already a transitive dep of langgraph |

No new packages need to be added to `pyproject.toml`.

---

## Testing plan

### Unit tests (`test_agent_stream.py`)

1. **`emit_event` with no queue set**: calling `emit_event` when `_event_queue` ContextVar holds `None` is a no-op (no exception raised).
2. **`emit_event` with queue set**: token is placed onto the queue.
3. **synthesizer_node with queue**: mock OpenAI to return 3 token deltas; assert 3 `token` events appear in the queue and `final_output` equals their concatenation.
4. **synthesizer_node with empty chunks**: returns `{"final_output": "No relevant documents..."}` and emits no tokens.
5. **router_node emits node_update**: mock OpenAI to return `"simple"`; assert first queue item is `{"type": "node_update", "node": "router_node", "status": "running"}`.
6. **executor_node emits tool_call events**: mock `TOOL_REGISTRY["document_retrieval"]`; assert `tool_call` events with `status: "running"` and `status: "complete"` appear; assert `sources` event appears with `chunks`.
7. **Ingest path SSE**: given a graph that classifies as `"ingest"`, the SSE output contains exactly one `node_update` (router) and one `done` with `message_id: null`.
8. **Error path SSE**: if the graph task raises an unhandled exception, the SSE output contains exactly one `error` event.

### Integration tests (existing suite must stay green)

9. **57 existing agent tests still pass** (`pytest backend/tests/` must report 57 passed with the refactored node functions).

### Manual verification steps

- (a) Start backend with a valid `.env` (OPENAI_API_KEY set).
- (b) Authenticate via the frontend, open a conversation, send a real financial question.
- (c) Confirm the chat UI streams tokens live as they arrive (not all at once).
- (d) Confirm `node_update` chips appear in the UI for each active node.
- (e) Confirm the `sources` panel populates with real document chunks.
- (f) Open the `messages` table in psql; confirm the assistant message is persisted with non-empty `content` and a non-null `agent_trace.langsmith_url`.
- (g) Open the LangSmith dashboard; confirm a new trace appears for the run.
- (h) Send a message like "please ingest this document"; confirm no assistant message is saved and the frontend triggers the upload flow.
- (i) Disconnect mid-stream; confirm no partial assistant message is saved and the server logs a cancellation, not an error.

---

## Observability

**Logs emitted per request**:
- `INFO chat_stream_started` — request received, graph about to launch
- `INFO user_message_saved` — user message persisted
- `INFO graph_invoke_started` — background task created, includes `run_id`
- `INFO chat_stream_completed` — assistant message persisted, includes token count
- `DEBUG graph_node_event` — one per SSE event emitted (conditionally, only in DEBUG level)
- `ERROR assistant_message_save_failed` — DB write failed
- `WARNING langsmith_trace_url_failed` — trace URL retrieval failed (non-fatal)
- `DEBUG summary_regenerated` — rolling summary updated

**Healthy state**: `chat_stream_completed` fires for every request, `langsmith_trace_url_failed` is absent.

**Unhealthy state**: `assistant_message_save_failed` or `error` events in SSE output indicate a problem. Repeated `langsmith_trace_url_failed` indicates LangSmith connectivity issues.

---

## Risks and open questions

1. **ContextVar propagation through LangGraph**: `asyncio.create_task` copies the current context, so the ContextVar set before `create_task` IS visible inside node coroutines. This must be verified empirically — if LangGraph internally creates sub-tasks or runs nodes in a threadpool, context propagation could break. If this occurs, fallback is a per-request dict keyed by `run_id` stored in a module-level `WeakValueDictionary`.

2. **`simple` path skips planner but not evaluator**: `route_after_router` sends `simple` queries directly to `executor_node`, which then always goes to `evaluator_node`. The evaluator might route back to executor on a retry. SSE must handle repeated `node_update` events for the same node name — the frontend should treat each emission independently (already correct per Phase 2 spec).

3. **LangSmith `read_run` latency**: the trace may not be immediately queryable after `ainvoke` returns. A short `asyncio.sleep(0.5)` before `read_run` may be needed. If the trace is still not ready, skip gracefully — the assistant message is still saved, just without a trace URL.

4. **`message_count % 6` approximation for summary trigger**: the spec counts total messages in the conversation. Two concurrent requests completing at the same message count could both trigger summary regeneration. For MVP this is acceptable. Phase 5 should use a DB advisory lock or a `last_summarised_at` column.

5. **No `model` validation**: `ChatRequest.model` is an unconstrained string. Invalid model names will cause the OpenAI call inside the synthesizer to fail, which is caught and emits an `error` event. Phase 5 should add an allowlist validator.

6. **`executor_node` tool_call events for `simple` path**: in the simple path, executor runs a single anonymous `document_retrieval` call (not via a plan). The current implementation does not have a `step_id` concept for the simple path. For simple queries, emit a single `tool_call` event with `step_id: "retrieval"` before and after the call. Deferred from complex-path behaviour for clarity.
