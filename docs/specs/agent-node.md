# Spec: Agent Nodes

## Goal
Implement the five executable computation units of the LangGraph financial research agent — Router, Planner, Executor, Evaluator, and Synthesizer — so that the `AgentState` contract established in `feature/agent-state` has runnable logic behind it.

## Background
`feature/agent-state` (PR #9, merged to main) defined `AgentState`, `PlanStep`, `RecentMessage`, `ChunkDict`, and `MemoryManager` in `app/agent/state.py`. The tool layer (PR #7) provides five tools (`financial_calculator`, `financial_data`, `news_fetch`, `document_retrieval`, `sec_filing`) accessible via `TOOL_REGISTRY` in `app/tools/__init__.py`.

Nothing calls those tools today; there is no routing, planning, retrieval evaluation, or synthesis logic. The `feature/agent-graph` feature (next) will wire these nodes into a LangGraph `StateGraph`, but it cannot do so until the nodes exist as plain async callables.

Key prior constraints carried forward:
- All `AgentState` values must remain JSON-serializable primitives (no ORM objects, no Pydantic instances, no `uuid.UUID`, no `datetime`).
- `structlog` is already installed and used in `state.py`; all logging must use it.
- `settings.OPENAI_API_KEY` is the canonical way to read the key (`app/config.py`).

## Scope

### In scope
- `app/agent/router.py` — `router_node` async callable
- `app/agent/planner.py` — `planner_node` async callable
- `app/agent/executor.py` — `executor_node` async callable
- `app/agent/evaluator.py` — `evaluator_node` async callable
- `app/agent/synthesizer.py` — `synthesizer_node` async callable
- `app/agent/__init__.py` — updated to export all five node functions
- `tests/agent/test_nodes.py` — unit tests for all five nodes with mocked OpenAI and mocked tool registry

### Out of scope
- LangGraph `StateGraph` definition, conditional edges, or `CompiledGraph` — that is `feature/agent-graph`
- SSE token streaming from the Synthesizer — that is `feature/agent-stream`
- LangSmith tracing integration — that is `feature/agent-stream`
- Any changes to API routes, the chat endpoint, or SSE infrastructure
- Frontend changes of any kind
- Alembic migrations or any DB schema changes
- `MemoryManager.regenerate_summary` trigger — the graph entry point calls this, not any node
- Redis or external state persistence
- Rate limiting, token budget enforcement, or cost tracking — that is Phase 5
- Importing or using LangGraph in any file created by this feature

## User flow
This feature has no direct end-user flow. The consumer is the LangGraph graph (built in `feature/agent-graph`). The intended runtime flow, for context:

1. Graph entry point populates a full `AgentState` dict and calls `router_node(state)`.
2. `router_node` returns `{"classification": "simple"|"complex"|"ingest"}`. Graph merges this into state.
3. A conditional edge routes to `planner_node` (complex) or directly to `executor_node` (simple/ingest).
4. `planner_node` returns `{"plan": [...]}`. Graph merges. Edge routes to `executor_node`.
5. `executor_node` runs tools, returns `{"tool_results": {...}, "retrieved_chunks": [...], "reranked_chunks": [...]}`.
6. `evaluator_node` scores retrieval quality. If score < 0.6 and retry < 2, reformulates query and the graph loops back to `executor_node`. Otherwise proceeds.
7. `synthesizer_node` produces `{"final_output": str}` from chunks.

Nodes in isolation (how they are called in tests): each node is called with a hand-constructed `AgentState` dict and its return value is asserted.

## Detailed requirements

### General (all nodes)
1. Every node must have the signature `async def <name>_node(state: AgentState) -> dict`.
2. Returned dicts must contain only JSON-serializable primitives (str, int, float, bool, None, list of those, dict of those). No ORM objects, no Pydantic models, no `uuid.UUID`, no `datetime`.
3. All OpenAI client instantiation must happen inside the node function body using `openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)`. No module-level client singletons.
4. All logging must use `structlog.get_logger(__name__)`. No `print` statements.
5. If any unhandled exception occurs inside a node, the node must catch it, log it at `error` level with the original exception bound, and return `{"error": str(exception)}`.
6. No LangGraph imports in any file in this feature.
7. Tool calls must go through `TOOL_REGISTRY` from `app/tools` — never call tool classes directly.

### Router node (`app/agent/router.py`)
8. Reads `state["query"]`, `state["recent_messages"]`, `state["conversation_summary"]`, and `state["analyst_profile"]` from state.
9. Makes exactly one `gpt-4o-mini` chat completion call with a system prompt that includes few-shot examples classifying queries into `"simple"`, `"complex"`, or `"ingest"`.
10. Returns `{"classification": <value>}` where value is one of `Literal["simple", "complex", "ingest"]`.
11. If the model returns an unrecognized classification string, defaults to `"complex"` and logs a `warning` event `router_unknown_classification` with the raw model output bound.
12. Logs a `debug` event `router_classified` with `classification` and `query_length` (character count) bound.

### Planner node (`app/agent/planner.py`)
13. Reads `state["query"]`, `state["classification"]`, and `state["analyst_profile"]` from state.
14. If `state["classification"] != "complex"`, returns `{"plan": []}` immediately without making any LLM call.
15. Makes exactly one `gpt-4o-mini` chat completion call with a JSON-mode system prompt instructing the model to decompose the query into a list of `PlanStep` dicts.
16. Each returned `PlanStep` must be a dict with keys `id` (str), `tool_name` (str), `input_template` (str), `dependencies` (list of str).
17. `tool_name` in each step must be one of the five registered tool names: `"financial_calculator"`, `"financial_data"`, `"news_fetch"`, `"document_retrieval"`, `"sec_filing"`. Steps with unrecognized tool names are dropped and a `warning` is logged.
18. Validates that no step's `dependencies` reference a step `id` that does not exist in the plan. If a bad dependency is found, drops the step and logs a `warning`.
19. Returns `{"plan": [<validated PlanStep dicts>]}`.
20. Logs a `debug` event `planner_plan_created` with `step_count` bound.

### Executor node (`app/agent/executor.py`)
21. Reads `state["plan"]`, `state["query"]`, `state["tool_results"]`, `state["retrieved_chunks"]`, `state["classification"]`, `state["user_id"]`, and `state["analyst_profile"]` from state.
22. For `classification == "simple"` or `classification == "ingest"`: calls the `document_retrieval` tool from `TOOL_REGISTRY` with the query and `user_id`. No plan is used.
23. For `classification == "complex"`: executes plan steps in dependency order. Steps with no unfulfilled dependencies are gathered and run concurrently via `asyncio.gather`. Each step's `input_template` is rendered by substituting `{query}` and `{user_id}` with their state values, plus any `{<step_id>}` placeholders with the string representation of the result of that dependency step.
24. Stores each tool result in a local `tool_results` dict keyed by step `id`. At the end, merges with `state["tool_results"]` (state value wins on key collision).
25. Collects all results from calls to `document_retrieval` into `retrieved_chunks` as a flat list of `ChunkDict` dicts. Deduplicates by `content` field (first occurrence wins).
26. Sets `reranked_chunks` equal to `retrieved_chunks` in this feature (reranking is a future concern). The field must be populated so downstream nodes can read it.
27. Returns `{"tool_results": <merged dict>, "retrieved_chunks": <list>, "reranked_chunks": <list>}`.
28. If a single tool call raises `ToolError`, logs the error at `warning` level with `tool_name` and `step_id` bound, stores `{"error": str(exception)}` as that step's result, and continues executing remaining steps.
29. Logs a `debug` event `executor_completed` with `step_count`, `chunk_count`, and `error_count` bound.

### Evaluator node (`app/agent/evaluator.py`)
30. Reads `state["retrieved_chunks"]`, `state["reranked_chunks"]`, `state["query"]`, and `state["retry_count"]` from state.
31. If `reranked_chunks` is empty, returns `{"retrieval_quality_score": 0.0, "retry_count": state["retry_count"], "query": state["query"]}` without making any LLM call.
32. Makes exactly one `gpt-4o-mini` chat completion call with a system prompt asking the model to return a single float between 0.0 and 1.0 representing retrieval quality. The prompt provides the query and up to 5 chunks (truncated to 500 characters each).
33. Parses the response as a float. If parsing fails or value is outside [0.0, 1.0], clamps to the nearest valid value and logs a `warning` event `evaluator_score_parse_warning` with the raw response bound.
34. If `score >= 0.6` OR `state["retry_count"] >= 2`: returns `{"retrieval_quality_score": score, "retry_count": state["retry_count"], "query": state["query"]}`. Does not modify `query`.
35. If `score < 0.6` AND `state["retry_count"] < 2`: makes a second `gpt-4o-mini` call to generate a reformulated query string. Increments `retry_count` by 1. Returns `{"retrieval_quality_score": score, "retry_count": state["retry_count"] + 1, "query": <reformulated query>}`.
36. Logs a `debug` event `evaluator_scored` with `score`, `retry_count`, and `will_retry` (bool) bound.

### Synthesizer node (`app/agent/synthesizer.py`)
37. Reads `state["reranked_chunks"]`, `state["query"]`, `state["conversation_summary"]`, `state["recent_messages"]`, `state["analyst_profile"]`, and `state["model"]` from state.
38. Uses `state.get("model") or "gpt-4o"` as the model name.
39. System prompt must explicitly instruct the model:
    - To base its answer only on the provided document chunks.
    - To refuse buy/sell/hold recommendations and any financial advice. If asked for such advice, respond with a clear disclaimer and describe only what the documents say.
    - To format inline citations as `[1]`, `[2]`, etc., mapped to the order of `reranked_chunks`.
    - To acknowledge the analyst's profile (role, focus areas) in how it frames findings.
40. Builds the user message by concatenating: `conversation_summary`, last 3 `recent_messages`, the query, and up to 10 chunks (full content).
41. Makes a streaming OpenAI chat completion call (`stream=True`). Collects all tokens and joins them into a single string. Does NOT yield tokens in this feature — SSE streaming is wired in `feature/agent-stream`.
42. Returns `{"final_output": <full response string>}`.
43. If `reranked_chunks` is empty, returns `{"final_output": "No relevant documents were found to answer this query."}` without making any LLM call.
44. Logs a `debug` event `synthesizer_completed` with `model`, `chunk_count`, and `output_length` (character count) bound.

## Data model changes
None. This feature creates no new DB tables and runs no migrations. All state lives in `AgentState` dicts passed between nodes at runtime.

## API contracts
None. This feature adds no new HTTP endpoints. The nodes are internal callables with no HTTP surface.

## Component and file structure

### Backend — new files
| File | Purpose |
|---|---|
| `backend/app/agent/router.py` | `router_node` callable — classifies query into simple/complex/ingest |
| `backend/app/agent/planner.py` | `planner_node` callable — decomposes complex queries into a DAG of PlanSteps |
| `backend/app/agent/executor.py` | `executor_node` callable — runs tool calls, respects plan dependencies, gathers parallel steps |
| `backend/app/agent/evaluator.py` | `evaluator_node` callable — scores retrieval quality, triggers query reformulation |
| `backend/app/agent/synthesizer.py` | `synthesizer_node` callable — produces grounded final answer with citations |

### Backend — modified files
| File | Change |
|---|---|
| `backend/app/agent/__init__.py` | Export all five node functions so `feature/agent-graph` can import from `app.agent` |

### Tests — new files
| File | Purpose |
|---|---|
| `backend/tests/agent/test_nodes.py` | Unit tests for all five nodes; OpenAI and tool registry mocked throughout |

### Config — no changes
`app/config.py` already has `OPENAI_API_KEY`. No new env vars needed.

## External dependencies

| Dependency | Purpose | If unavailable | Notes |
|---|---|---|---|
| `openai` (PyPI: `openai>=1.0`) | AsyncOpenAI client for all LLM calls | Node catches exception, returns `{"error": ...}` | Already in `requirements.txt` via `feature/agent-state` |
| `structlog` | Structured logging | — would fail at import; already required | Already installed |
| `app/tools/TOOL_REGISTRY` | All five tools accessed via registry | `ToolError` caught per requirement 28 | Already implemented in `feature/tool-layer` |
| `app/agent/state.py` | `AgentState`, `PlanStep`, `ChunkDict` TypedDicts | Import error at startup | Already merged via `feature/agent-state` |

No new third-party packages are introduced by this feature.

## Testing plan

### Unit tests (`tests/agent/test_nodes.py`)
All tests mock `openai.AsyncOpenAI` via `unittest.mock.AsyncMock` and mock `TOOL_REGISTRY` by patching `app.agent.executor.TOOL_REGISTRY` (and similar per node). No real OpenAI calls. No real DB.

**Router node**
- Happy path: model returns `"simple"` → `classification == "simple"`
- Happy path: model returns `"complex"` → `classification == "complex"`
- Happy path: model returns `"ingest"` → `classification == "ingest"`
- Unknown classification: model returns `"unknown_value"` → defaults to `"complex"`, no exception raised
- Exception path: `AsyncOpenAI` raises `openai.OpenAIError` → node returns `{"error": ...}`

**Planner node**
- Non-complex classification: `classification == "simple"` → returns `{"plan": []}` with zero LLM calls
- Happy path: model returns valid JSON with two steps, both have valid `tool_name` and valid dependencies → plan has two steps
- Invalid tool name: one step has `tool_name == "nonexistent"` → step dropped, plan has one step
- Invalid dependency: one step references a non-existent step id in `dependencies` → step dropped
- Exception path: `AsyncOpenAI` raises → `{"error": ...}`

**Executor node**
- Simple classification: calls `document_retrieval` once, returns chunks in both `retrieved_chunks` and `reranked_chunks`
- Complex classification with two independent steps: both tools called via `asyncio.gather` (assert both called, assert results in `tool_results`)
- Complex classification with dependency chain (step B depends on step A): step A runs first, step B runs after, step B's input template rendered with step A's result
- Tool raises `ToolError`: result stored as `{"error": ...}` for that step, other steps still execute
- Exception path: unexpected exception → `{"error": ...}`

**Evaluator node**
- Empty chunks: returns score 0.0 without LLM call
- Score >= 0.6: returns score unchanged, `query` unchanged, `retry_count` unchanged
- Score < 0.6 and retry_count == 0: increments retry_count to 1, reformulated query returned
- Score < 0.6 and retry_count == 2: does NOT retry (retry_count stays at 2), query unchanged
- Unparseable score string from model: clamps, logs warning, no exception raised
- Exception path: `AsyncOpenAI` raises → `{"error": ...}`

**Synthesizer node**
- Empty chunks: returns `{"final_output": "No relevant documents were found..."}` with zero LLM calls
- Happy path: streaming response collected into single string, `final_output` is non-empty
- Model field absent/None in state: defaults to `"gpt-4o"`
- System prompt contains no-advice instruction: assert `"buy"` / `"sell"` / `"hold"` refusal language appears in the system prompt passed to the mock
- Exception path: `AsyncOpenAI` raises → `{"error": ...}`

### Manual verification steps
1. Activate venv: `venv\Scripts\activate`
2. Run `pytest tests/agent/test_nodes.py -v` — all tests must pass with no real network calls
3. In a Python REPL, import each node: `from app.agent import router_node, planner_node, executor_node, evaluator_node, synthesizer_node` — no import errors
4. Construct a minimal `AgentState`-shaped dict and call `asyncio.run(router_node(state))` — confirm it returns a dict with `"classification"` key (requires a valid `OPENAI_API_KEY` in `.env` for this step)

## Observability

### Logged events (structlog, all at `debug` unless noted)

| Event key | Node | Fields |
|---|---|---|
| `router_classified` | Router | `classification`, `query_length` |
| `router_unknown_classification` | Router | `raw_output` — level: `warning` |
| `planner_plan_created` | Planner | `step_count` |
| `planner_invalid_tool` | Planner | `tool_name`, `step_id` — level: `warning` |
| `planner_invalid_dependency` | Planner | `step_id`, `bad_dependency` — level: `warning` |
| `executor_completed` | Executor | `step_count`, `chunk_count`, `error_count` |
| `executor_tool_error` | Executor | `tool_name`, `step_id`, `error` — level: `warning` |
| `evaluator_scored` | Evaluator | `score`, `retry_count`, `will_retry` |
| `evaluator_score_parse_warning` | Evaluator | `raw_response` — level: `warning` |
| `synthesizer_completed` | Synthesizer | `model`, `chunk_count`, `output_length` |
| `<node>_error` | All | `error` — level: `error` (emitted from exception handler in each node) |

### Healthy vs unhealthy state
- **Healthy**: all five nodes importable; unit tests green; Router returns one of the three valid classifications; Synthesizer `final_output` is non-empty for non-empty chunk input.
- **Unhealthy**: any node returns `{"error": ...}` in production — indicates OpenAI API failure or tool failure. The graph (next feature) will check for the `error` field and halt the run.

## Risks and open questions

1. **Input template rendering in Executor**: the spec describes `{query}` and `{user_id}` substitution plus `{<step_id>}` placeholders. The exact template syntax needs to be locked down before implementation — Python `.format_map()` is the simplest approach, but step results may be complex dicts. Decision deferred to implementation; use `str()` coercion on dependency results.

2. **Reranking is a stub**: `executor_node` sets `reranked_chunks = retrieved_chunks`. A future feature will replace this with a real reranker (Cohere, cross-encoder, etc.). The field name and type are locked now; only the value changes.

3. **Chunk deduplication in Executor**: deduplication by `content` equality is naive. Two chunks with the same content from different documents (unlikely but possible) will be collapsed. Acceptable for v1.

4. **JSON mode reliability for Planner**: `gpt-4o-mini` in JSON mode occasionally emits valid JSON that doesn't match the expected schema. The validation step (requirements 17–18) guards against the worst cases, but malformed `input_template` values will pass through unchecked until a step execution fails at runtime.

5. **Synthesizer citation mapping**: the spec says citations are `[1]`, `[2]` mapped to chunk indices. If the model hallucinates a citation index beyond `len(reranked_chunks)`, the response will contain a dangling reference. This is a known limitation; citation verification is out of scope.

6. **No-advice enforcement**: requirement 39 enforces the no-advice instruction at the prompt level only. A sufficiently adversarial user query could still elicit financial advice from the model. A content-filter layer is out of scope for this feature.
