# Spec: Agent State

## Goal
Define the shared `AgentState` TypedDict that wires together the five LangGraph nodes (Router, Planner, Executor, Evaluator, Synthesizer), and implement a memory manager that loads conversation history from the database into state.

## Background
The tool layer (Phase 3) delivered five tools and a registry. The next phase is building the LangGraph agent graph. Before any node can be implemented, all nodes need a shared data contract — a single TypedDict that every node reads from and writes to. Without it, each node would invent its own input/output shape and the graph could not be wired together.

The conversation model already has a `rolling_summary` column (`Text`, nullable) and a `messages` relationship with `role` and `content` columns. The analyst profile schema (`ProfileRead`) defines the fields available in `analyst_profile`. The `ChunkResult` Pydantic model from `schemas/document.py` defines the shape of retrieval results. All state values must be plain JSON-serializable Python objects — no ORM model instances, no Pydantic objects — because LangGraph's in-memory checkpointer serializes state to JSON between node invocations.

## Scope

### In scope
- `AgentState` TypedDict defined in `backend/app/agent/state.py` with all 15 fields typed exactly as specified.
- Sub-TypedDicts for nested structures: `PlanStep`, `RecentMessage`, `ChunkDict`.
- A `MemoryManager` class in `backend/app/agent/state.py` with:
  - `load_memory(db, user_id, conversation_id) -> dict` — queries the DB for the rolling summary and the three most-recent messages, returns a partial `AgentState`-shaped dict.
  - `regenerate_summary(prior_summary, new_messages, openai_client) -> str` — calls `gpt-4o-mini` to produce a rolling summary under 300 words given the prior summary and a list of new message dicts.
- Unit tests in `backend/tests/agent/test_state.py` covering all code paths with mocked DB and mocked OpenAI.
- `backend/app/agent/__init__.py` exporting `AgentState` and `MemoryManager`.

### Out of scope
- LangGraph graph definition or any node implementation.
- Any changes to API routes or the SSE document-status stream.
- LangSmith tracing or any other observability integration beyond structlog.
- Redis state persistence — LangGraph's in-memory checkpointer only.
- Alembic migrations — the `rolling_summary` column already exists.
- UI changes of any kind.
- Enforcing `retry_count <= 2` inside state — the graph edges own that logic.
- Pydantic validation of state fields — TypedDict only, no runtime validation.

## User flow
This feature has no user-facing flow. The internal flow is:

1. A LangGraph graph invocation begins. The Router node receives a raw `query` and `user_id` from the chat endpoint.
2. Before the Router runs, the graph's entry point calls `MemoryManager.load_memory(db, user_id, conversation_id)` to hydrate `conversation_summary` and `recent_messages` into the initial state dict.
3. The Router node reads `query`, `analyst_profile`, `recent_messages`, and `conversation_summary` from state and writes `classification` back.
4. The Planner reads `query` and `classification`, writes `plan`.
5. The Executor reads `plan`, calls tools, writes `tool_results`.
6. The Evaluator reads `tool_results`, writes `retrieval_quality_score` and optionally increments `retry_count`.
7. The Synthesizer reads all populated fields, writes `final_output`.
8. After the graph completes, the caller persists the new message to DB and, if warranted, calls `MemoryManager.regenerate_summary(prior_summary, new_messages, openai_client)` to update `rolling_summary` on the `Conversation` row.

**Error path**: any node may write a non-`None` string to `error`. Graph edge logic (out of scope here) routes to a terminal error state when `error` is set.

**Retry path**: the Evaluator may increment `retry_count`. Graph edges (out of scope) loop back to the Executor up to twice. The `retry_count` field in state allows values 0, 1, 2; the graph prevents a third increment.

## Detailed requirements

### AgentState TypedDict
1. `AgentState` is a `TypedDict` (from `typing`) defined at module level in `app/agent/state.py`.
2. Field `user_id: str` — always present, never empty.
3. Field `conversation_id: str` — always present, never empty.
4. Field `analyst_profile: dict` — free-form dict; populated from `ProfileRead.model_dump()` before entering state.
5. Field `query: str` — the raw user query string; always present.
6. Field `classification: Literal["simple", "complex", "ingest"]` — set by the Router node; uses `typing.Literal`.
7. Field `plan: list[PlanStep]` — set by the Planner; empty list `[]` as initial value.
8. Field `tool_results: dict[str, Any]` — keyed by `PlanStep.id`; empty dict `{}` as initial value.
9. Field `retrieved_chunks: list[ChunkDict]` — raw retrieval results; max 20 items enforced by the tool (not by state).
10. Field `reranked_chunks: list[ChunkDict]` — reranked subset; max 5 items enforced by the Evaluator (not by state).
11. Field `retrieval_quality_score: float` — value in `[0.0, 1.0]`; `0.0` as initial value.
12. Field `retry_count: int` — incremented by the Evaluator; `0` as initial value; the type allows any non-negative int.
13. Field `conversation_summary: str` — rolling summary from DB; `""` as initial value when no prior summary exists.
14. Field `recent_messages: list[RecentMessage]` — last 3 messages loaded from DB; `[]` as initial value when no prior messages exist.
15. Field `final_output: str` — written by the Synthesizer; `""` as initial value.
16. Field `error: str | None` — `None` as initial value; set by any node on unrecoverable failure.
17. Field `model: str` — OpenAI model identifier for the Synthesizer; default `"gpt-4o"`.

### Sub-TypedDicts
18. `PlanStep` TypedDict has fields: `id: str`, `tool_name: str`, `input_template: str`, `dependencies: list[str]`.
19. `RecentMessage` TypedDict has fields: `role: str`, `content: str`.
20. `ChunkDict` TypedDict has fields: `content: str`, `metadata: dict | None`, `score: float`.
21. All three sub-TypedDicts are defined in `app/agent/state.py` and exported from `app/agent/__init__.py`.

### Serialization contract
22. No ORM model instances, Pydantic model instances, `uuid.UUID` objects, or `datetime` objects may appear inside `AgentState` at runtime. Callers must convert UUIDs to `str(...)` and datetimes to `.isoformat()` before populating state.
23. The module-level docstring of `state.py` documents this constraint explicitly.

### MemoryManager.load_memory
24. Signature: `async def load_memory(self, db: AsyncSession, user_id: str, conversation_id: str) -> dict`.
25. Queries the `conversations` table for a row matching both `id == conversation_id` AND `user_id == user_id` and `deleted_at IS NULL`. If not found, raises `ValueError("conversation not found")`.
26. Returns `{"conversation_summary": row.rolling_summary or "", "recent_messages": [...]}`.
27. `recent_messages` is built from the three most-recent `Message` rows ordered by `created_at DESC`, then reversed to chronological order (oldest first). Each message is serialized as `{"role": message.role.value, "content": message.content}`.
28. If the conversation has zero messages, `recent_messages` is `[]`.
29. The DB query for messages uses `ORDER BY created_at DESC LIMIT 3` to avoid loading all messages into memory.
30. All DB access uses the `AsyncSession` dependency passed in — no new engine or session is created inside the method.
31. Logs `memory_loaded` at `DEBUG` level via structlog with fields `conversation_id`, `message_count`, `has_summary`.

### MemoryManager.regenerate_summary
32. Signature: `async def regenerate_summary(self, prior_summary: str, new_messages: list[RecentMessage], openai_client: openai.AsyncOpenAI) -> str`.
33. Calls `openai_client.chat.completions.create` with model `"gpt-4o-mini"`.
34. System prompt instructs the model to produce a rolling financial research conversation summary in 300 words or fewer, incorporating both the prior summary and the new messages.
35. The new messages are serialized into the user message as `role: content` lines.
36. If `prior_summary` is empty, the prompt omits the "prior summary" section and summarizes only `new_messages`.
37. Returns the `.choices[0].message.content.strip()` string.
38. Does not call the DB — the caller is responsible for persisting the returned string back to `Conversation.rolling_summary`.
39. Logs `summary_regenerated` at `DEBUG` level with field `word_count` (count of words in returned string).
40. If `new_messages` is an empty list, returns `prior_summary` unchanged without calling OpenAI.

### General
41. `app/agent/__init__.py` exports: `AgentState`, `PlanStep`, `RecentMessage`, `ChunkDict`, `MemoryManager`.
42. No import of any LangGraph symbol anywhere in `app/agent/state.py` or `app/agent/__init__.py`.
43. No circular imports: `state.py` may import from `app.database`, `app.models.conversation`, and standard library only (plus `openai` and `structlog`).

## Data model changes
No new tables or columns. The existing schema already provides everything needed:

| What | Where | Notes |
|---|---|---|
| `conversations.rolling_summary` | `app/models/conversation.py:29` | `Text`, nullable — read and written by the memory manager |
| `messages.role` | `app/models/conversation.py:48` | `MessageRole` enum — serialized as `.value` |
| `messages.content` | `app/models/conversation.py:52` | `Text` |
| `messages.created_at` | `app/models/conversation.py:55` | used for `ORDER BY … DESC LIMIT 3` |

No Alembic migration is required.

## API contracts
No new or modified HTTP endpoints in this phase. The memory manager is a pure service class called internally by the graph entry point (implemented in a future phase).

## Component and file structure

### New files — backend
| File | Purpose |
|---|---|
| `backend/app/agent/__init__.py` | Package init; re-exports `AgentState`, `PlanStep`, `RecentMessage`, `ChunkDict`, `MemoryManager` |
| `backend/app/agent/state.py` | Defines all TypedDicts and the `MemoryManager` class |
| `backend/tests/agent/__init__.py` | Empty package init for the test sub-package |
| `backend/tests/agent/test_state.py` | All unit tests for `state.py` |

### Modified files — none
No existing files are modified in this phase.

### Directory structure after this phase
```
backend/app/agent/
    __init__.py
    state.py

backend/tests/agent/
    __init__.py
    test_state.py
```

## External dependencies
| Dependency | Purpose | If unavailable | Notes |
|---|---|---|---|
| `openai` Python SDK | `regenerate_summary` calls `gpt-4o-mini` | Tests mock the client; production callers should handle `openai.OpenAIError` | Already a project dependency via `requirements.txt` |
| `sqlalchemy[asyncio]` / `asyncpg` | `load_memory` queries Postgres | Tests use `AsyncMock`; production DB must be reachable | Already a project dependency |
| `structlog` | Logging inside `MemoryManager` | No fallback needed — it's always available | Already a project dependency |

`gpt-4o-mini` is used (not `gpt-4o`) because summary regeneration is a lightweight summarization task; the Synthesizer uses `gpt-4o` and that model choice lives in `AgentState.model`.

## Testing plan

### Unit tests (`backend/tests/agent/test_state.py`)

**TypedDict shape tests**
- `test_agent_state_fields` — construct an `AgentState`-shaped dict with all 17 fields present and assert no `TypeError` is raised (TypedDicts are checked structurally, not at runtime, but this confirms all field names are correct).
- `test_plan_step_fields` — same for `PlanStep`.
- `test_recent_message_fields` — same for `RecentMessage`.
- `test_chunk_dict_fields` — same for `ChunkDict`.

**MemoryManager.load_memory**
- `test_load_memory_happy_path` — mock `AsyncSession.execute` to return a `Conversation` with `rolling_summary="Prior summary"` and three `Message` rows; assert returned dict equals `{"conversation_summary": "Prior summary", "recent_messages": [{"role": "user", "content": "..."}, ...]}` with messages in chronological order.
- `test_load_memory_no_summary` — conversation has `rolling_summary=None`; assert `conversation_summary == ""`.
- `test_load_memory_no_messages` — conversation exists but has zero messages; assert `recent_messages == []`.
- `test_load_memory_fewer_than_3_messages` — conversation has 2 messages; assert `recent_messages` has length 2.
- `test_load_memory_conversation_not_found` — mock returns no row; assert `ValueError("conversation not found")` is raised.
- `test_load_memory_user_id_scoped` — assert the SQLAlchemy `WHERE` clause includes both `conversation_id` and `user_id` filters (inspect the call args on the mocked `execute`).

**MemoryManager.regenerate_summary**
- `test_regenerate_summary_happy_path` — mock `openai_client.chat.completions.create` to return a response with content `"New summary"`; assert the return value is `"New summary"`.
- `test_regenerate_summary_with_prior_summary` — assert the system or user prompt passed to OpenAI includes the `prior_summary` text.
- `test_regenerate_summary_no_prior_summary` — `prior_summary=""` ; assert the prior summary section is absent from the prompt.
- `test_regenerate_summary_empty_messages` — `new_messages=[]`; assert OpenAI is NOT called and the return value equals `prior_summary` unchanged.
- `test_regenerate_summary_strips_whitespace` — mock OpenAI returns `"  padded  "`; assert return value is `"padded"`.
- `test_regenerate_summary_uses_mini_model` — assert the `model` kwarg passed to OpenAI is `"gpt-4o-mini"`.

### Manual verification
1. Start the backend: `uvicorn app.main:app --reload`.
2. In a Python REPL or test script, import `from app.agent import AgentState, MemoryManager` — confirm no import error.
3. Construct a minimal `AgentState`-shaped dict with all fields; confirm it is `json.dumps`-serializable without error.

## Observability
- `memory_loaded` logged at `DEBUG` with `conversation_id: str`, `message_count: int`, `has_summary: bool`.
- `summary_regenerated` logged at `DEBUG` with `word_count: int`.
- No metrics or traces in this phase — LangSmith tracing is out of scope.
- **Healthy state**: both functions complete without exception and return the expected types.
- **Unhealthy state**: `load_memory` raises `ValueError` on missing conversation; `regenerate_summary` propagates `openai.OpenAIError` to the caller without swallowing it.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| `rolling_summary` column update cadence not defined | Deferred to the node implementation phase; `regenerate_summary` only generates the string — the caller decides when to persist |
| Summary drift over long conversations | 300-word cap plus rolling (not append) strategy limits context bleed; acceptable for MVP |
| `gpt-4o-mini` hallucinating prior conversation details | Caller passes `prior_summary` verbatim; model is instructed to summarize, not invent |
| TypedDict does not enforce types at runtime | Intentional — runtime validation would add overhead on every state mutation; nodes are responsible for writing correct types |
| `ORDER BY created_at DESC LIMIT 3` tie-breaking | Two messages with identical `created_at` are ordered non-deterministically; acceptable for MVP given wall-clock resolution in practice |
| Future LangGraph checkpointer change | State is a plain dict of primitives; switching from in-memory to Redis checkpointer requires no changes to this file |
