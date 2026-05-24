# Spec: Agent Graph

## Goal
Wire the five isolated agent node functions into a compiled LangGraph `StateGraph` that routes queries end-to-end — from classification through optional planning, retrieval, evaluation, and synthesis — so the FastAPI chat endpoint can call `await compiled_graph.ainvoke(initial_state)` and receive a completed `AgentState`.

---

## Background
`feature/agent-state` (merged) defined `AgentState` and `MemoryManager` in `app/agent/state.py`.
`feature/agent-nodes` (merged) implemented five async node functions:
- `router_node` — classifies the query as `simple`, `complex`, or `ingest`
- `planner_node` — decomposes complex queries into a `PlanStep` list
- `executor_node` — runs tools against the plan or a simple query
- `evaluator_node` — scores retrieval quality and reformulates the query if needed
- `synthesizer_node` — generates the final answer from retrieved chunks

All five are registered in `app/agent/__init__.py`. No graph wiring exists. The five nodes are disconnected functions with no shared orchestration, no conditional routing, and no retry loop. The FastAPI chat endpoint cannot yet invoke them as a unit.

**Critical pre-condition:** LangGraph is listed in neither `requirements.txt` nor the venv. It must be added before implementation begins.

---

## Scope

### In scope
- `backend/requirements.txt`: add `langgraph` with a pinned version
- `backend/app/agent/graph.py`: new file — `StateGraph` definition, routing functions, and module-level `compiled_graph`
- `backend/app/agent/__init__.py`: add `compiled_graph` to exports
- `backend/tests/agent/test_graph.py`: new file — unit tests for routing functions and integration tests for all four execution paths

### Out of scope
- SSE token streaming — `feature/agent-stream`
- LangSmith tracing — `feature/agent-stream`
- Replacing the fake SSE chat endpoint with the real graph — `feature/agent-stream`
- Redis checkpointer or any persistent graph state (in-memory only for MVP)
- LangGraph Studio or graph visualization
- `MemoryManager.regenerate_summary` — called by the chat endpoint, not the graph
- Document ingestion beyond routing to `END` (Celery handles the actual ingestion)
- Rate limiting or token budget enforcement
- Any frontend changes
- Alembic migrations

---

## User flow

### Happy path — simple query
1. Caller (FastAPI endpoint, future) calls `await compiled_graph.ainvoke(initial_state)` where `initial_state` is a fully populated `AgentState` with `retry_count: 0`.
2. Graph enters `router_node`. Node returns `{"classification": "simple"}`.
3. `route_after_router` reads `state["classification"]` → returns `"executor_node"`.
4. Graph enters `executor_node`. Node populates `tool_results`, `retrieved_chunks`, `reranked_chunks`.
5. Graph enters `evaluator_node`. Node scores retrieval quality. Score ≥ 0.6. Returns `{"retrieval_quality_score": <score>, "retry_count": 0, "query": <original>}`.
6. `route_after_evaluator` reads score ≥ 0.6 → returns `"synthesizer_node"`.
7. Graph enters `synthesizer_node`. Node populates `final_output`.
8. Graph reaches `END`. `ainvoke` returns the completed `AgentState`.

### Happy path — complex query
Steps 1–2 as above, except router returns `{"classification": "complex"}`.
3. `route_after_router` → `"planner_node"`.
4. `planner_node` populates `plan`.
5. Unconditional edge → `executor_node`. Continues as simple path from step 4.

### Ingest path
Router returns `{"classification": "ingest"}`.
`route_after_router` → `END` immediately. No other nodes run. Caller receives state with `classification: "ingest"` and no `final_output` (document ingestion is handled by Celery, not the graph).

### Retry path (low retrieval quality)
1–4 as simple path. After first `executor_node` run:
5. `evaluator_node` scores retrieval quality < 0.6. `retry_count` in state is 0, which is < 2, so `will_retry = True`. Evaluator reformulates query, returns `{"retrieval_quality_score": <score>, "retry_count": 1, "query": <reformulated>}`.
6. `route_after_evaluator`: score < 0.6 AND `retry_count` (now 1) < 2 → `"executor_node"`.
7. `executor_node` runs again with reformulated query.
8. `evaluator_node` runs again. Score < 0.6, `retry_count` in state is 1, which is < 2, so evaluator reformulates and returns `retry_count: 2`.
9. `route_after_evaluator`: score < 0.6 AND `retry_count` (now 2) < 2 = False → `"synthesizer_node"`.
10. `synthesizer_node` runs. Graph reaches `END`.

**Net result:** `executor_node` runs at most twice (original + 1 retry); `evaluator_node` runs at most twice.

### Error path
If any node returns `{"error": <message>}` in its dict, the error field is merged into `AgentState`. The graph does not short-circuit on errors — all nodes check `state.get("error")` internally (this is already implemented in the nodes). The graph itself adds no error-handling logic beyond what the nodes already provide.

---

## Detailed requirements

### Functional

1. `backend/app/agent/graph.py` must define and export a module-level name `compiled_graph` that is the result of calling `.compile()` on a `StateGraph(AgentState)` instance.
2. `from app.agent.graph import compiled_graph` must succeed with no import errors when the venv is active.
3. The graph entry point must be `router_node`.
4. After `router_node`, the conditional routing function `route_after_router(state: AgentState) -> str` must return:
   - `END` when `state["classification"] == "ingest"`
   - `"planner_node"` when `state["classification"] == "complex"`
   - `"executor_node"` for all other values (including `"simple"` and any unexpected fallback)
5. There must be an unconditional edge from `planner_node` to `executor_node`.
6. There must be an unconditional edge from `executor_node` to `evaluator_node`.
7. After `evaluator_node`, the conditional routing function `route_after_evaluator(state: AgentState) -> str` must return:
   - `"executor_node"` when `state.get("retrieval_quality_score", 1.0) < 0.6 AND state.get("retry_count", 0) < 2`
   - `"synthesizer_node"` in all other cases
8. There must be an unconditional edge from `synthesizer_node` to `END`.
9. The graph must use `async` execution: callers must use `await compiled_graph.ainvoke(state)`, never `compiled_graph.invoke(state)`.
10. The graph must not introduce any `asyncio.run()` calls or synchronous wrappers.
11. `app/agent/__init__.py` must export `compiled_graph` in its `__all__` list.
12. `langgraph` must be added to `backend/requirements.txt` with a pinned version (determined at implementation time by installing and running `pip show langgraph`).

### Error handling
13. The graph itself must not catch exceptions from nodes. Node-level exceptions propagate to the caller (`ainvoke` will raise). Node-level error handling is already implemented inside each node function.
14. The routing functions (`route_after_router`, `route_after_evaluator`) must use `.get()` with safe defaults for all state key reads so they do not raise `KeyError` if optional fields are absent.

### Security
15. The graph adds no auth or scoping logic. `user_id` is already in `AgentState` and enforced inside the node functions.
16. The graph must not log `AgentState` contents at any level (state may contain PII such as query text and analyst profile).

### Performance
17. No explicit latency target for MVP. The graph must not add synchronous blocking operations (e.g., `time.sleep`, `asyncio.run`) outside of node execution.

### Observability
18. `graph.py` must emit one `structlog` DEBUG log at module import time confirming compilation succeeded: `log.debug("agent_graph_compiled")`.
19. The two routing functions must emit one `structlog` DEBUG log each when called, recording the routing decision and the state field(s) used.

---

## Data model changes
None. `AgentState` is already defined in `app/agent/state.py` and is not modified by this feature.

---

## API contracts
None. The graph is an internal Python object, not a web endpoint. The FastAPI interface is wired in `feature/agent-stream`.

---

## Component and file structure

### Backend — new files
| File | Purpose |
|---|---|
| `backend/app/agent/graph.py` | Defines `StateGraph`, adds nodes and edges, exports `compiled_graph` |
| `backend/tests/agent/test_graph.py` | Unit tests for routing functions + integration tests for all four paths |

### Backend — modified files
| File | Change |
|---|---|
| `backend/requirements.txt` | Add `langgraph==<pinned version>` |
| `backend/app/agent/__init__.py` | Import and export `compiled_graph` |

### No frontend changes.

---

## External dependencies

| Dependency | Purpose | If unavailable | Notes |
|---|---|---|---|
| `langgraph` | `StateGraph`, `END`, conditional/unconditional edges | `ImportError` at startup — service will not boot | Must be pinned. Determine version by installing with pip at implementation time. |

LangGraph itself depends on `langchain-core`. Ensure no version conflicts with existing packages when installing.

---

## Testing plan

### Unit tests — routing functions
All tests in `tests/agent/test_graph.py`, no network calls, no real nodes.

**`route_after_router`**
- R1: `classification = "simple"` → returns `"executor_node"`
- R2: `classification = "complex"` → returns `"planner_node"`
- R3: `classification = "ingest"` → returns `END`
- R4: `classification = "UNKNOWN"` (unexpected value) → returns `"executor_node"` (safe fallback)
- R5: `classification` key missing from state (empty dict) → returns `"executor_node"` (`.get()` fallback)

**`route_after_evaluator`**
- E1: score = 0.9, retry_count = 0 → returns `"synthesizer_node"`
- E2: score = 0.5, retry_count = 0 → returns `"executor_node"`
- E3: score = 0.5, retry_count = 1 → returns `"executor_node"`
- E4: score = 0.5, retry_count = 2 → returns `"synthesizer_node"` (cap reached)
- E5: score = 0.6 (boundary, not < 0.6), retry_count = 0 → returns `"synthesizer_node"`
- E6: score = 0.0, retry_count = 3 → returns `"synthesizer_node"` (over cap)
- E7: both keys missing → returns `"synthesizer_node"` (score defaults to 1.0)

### Integration tests — graph path tracing
Each test builds a *fresh* `StateGraph` with the same edges/conditionals as the real graph but substitutes the five node functions with `AsyncMock` instances that record call order and return controlled state patches. This tests routing correctness without any OpenAI calls.

**I1: simple path**
- Mock router returns `classification: "simple"`; mock evaluator returns `retrieval_quality_score: 0.9, retry_count: 0`
- Assert call order: `[router, executor, evaluator, synthesizer]`
- Assert `result["final_output"]` is set (from mock synthesizer)

**I2: complex path**
- Mock router returns `classification: "complex"`
- Assert call order: `[router, planner, executor, evaluator, synthesizer]`

**I3: ingest path**
- Mock router returns `classification: "ingest"`
- Assert call order: `[router]` only — no other nodes called
- Assert `result.get("final_output")` is absent or `None`

**I4: retry path (1 retry)**
- Mock router returns `classification: "simple"`
- Mock evaluator: first call returns `retrieval_quality_score: 0.4, retry_count: 1`; second call returns `retrieval_quality_score: 0.4, retry_count: 2`
- Assert call order: `[router, executor, evaluator, executor, evaluator, synthesizer]`
- Assert executor was called exactly 2 times

**I5: retry exhausted before score improves**
Same as I4 but second evaluator call returns `retrieval_quality_score: 0.3, retry_count: 2` — verifies graph still routes to synthesizer (does not loop infinitely).

### Regression
- Run the existing 40 tests (`tests/agent/test_state.py` — 15 tests; `tests/agent/test_nodes.py` — 25 tests). All must pass without modification.
- Command: `pytest tests/agent/test_state.py tests/agent/test_nodes.py tests/agent/test_graph.py -v`

### Manual verification
```bash
# After activating venv and ensuring OPENAI_API_KEY is in .env:
cd backend
python -c "from app.agent.graph import compiled_graph; print('Graph compiled:', compiled_graph)"
```
Expected output: `Graph compiled: <langgraph.graph.state.CompiledStateGraph object at 0x...>`

---

## Observability

| Signal | Level | When |
|---|---|---|
| `agent_graph_compiled` | DEBUG | Once, at module import time, after `builder.compile()` succeeds |
| `route_after_router` decision | DEBUG | Each invocation — logs `classification` and the chosen next node |
| `route_after_evaluator` decision | DEBUG | Each invocation — logs `retrieval_quality_score`, `retry_count`, and chosen next node |

Healthy state: `agent_graph_compiled` appears in startup logs. No `agent_graph_compiled` means the import failed.

Unhealthy state: repeated `route_after_evaluator` logs with `executor_node` chosen more than twice per request indicates the retry cap is not working.

---

## Risks and open questions

### Risks
1. **LangGraph version conflicts.** Installing `langgraph` pulls `langchain-core` as a transitive dependency. If `langchain-core` conflicts with an existing package, the install will fail. Mitigation: install `langgraph` first with `--dry-run` or check for conflicts before pinning.

2. **LangGraph API instability.** The `StateGraph` / `add_conditional_edges` API has changed across minor LangGraph versions (e.g., the mapping parameter became optional in later versions). The spec is written against the stable pattern; implementation must match the installed version's actual API.

3. **`END` import path.** `END` is imported from `langgraph.graph`. If the installed version uses a different path (e.g., `langgraph.constants`), the import will fail. Verify at implementation time.

4. **Compiled graph is a module-level singleton.** If two concurrent requests share the same compiled graph instance, LangGraph's in-memory state handling must be re-entrant. For `ainvoke` with no checkpointer, LangGraph creates an independent run per call — this is safe. Confirm after installing the pinned version.

5. **Retry path test is sensitive to evaluator mock call count.** The integration test must carefully control which call of the evaluator mock returns which value (using `side_effect` list). A mistake in mock setup will give a false positive.

### Open questions
1. Should `graph.py` also export `route_after_router` and `route_after_evaluator` for direct unit testing, or should the unit tests import them from the module directly? (Recommendation: export them in `__all__` — makes testing intent explicit.)

2. The `retry_count` boundary: the existing evaluator code checks `state["retry_count"] < 2` internally and the graph edge also checks `retry_count < 2`. This means the executor runs at most 2 times (original + 1 retry). If the product requirement is 3 total executions (2 retries), the edge condition must change to `retry_count <= 2` — confirm with stakeholder before implementing.

3. What should the graph do if `error` is set in state mid-run (e.g., router fails and sets `error`)? Currently the graph does not short-circuit. Should there be an error-handling terminal node? Deferred to `feature/agent-stream` unless the team decides it belongs here.
