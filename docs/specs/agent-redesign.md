# Spec: Full Agent Redesign for FinCopilot 2.0

## Goal
Replace the broken LangGraph research agent with a two-path architecture (simple → tool-selector → synthesizer; complex → planner → executor → synthesizer) so that tool results actually reach the synthesizer, failures degrade gracefully into the answer instead of crashing the run, and errors become visible to the financial analyst using the chat.

## Background

### What exists today
The agent is a LangGraph `StateGraph` (`backend/app/agent/graph.py`) over an `AgentState` TypedDict (`backend/app/agent/state.py`). The current topology is:

```
router → (simple|ingest) → executor → evaluator → (retry → executor | synthesizer) → END
       → (complex)        → planner → executor → evaluator → … → synthesizer → END
       → (ingest)         → END
```

Nodes:
- `router_node` — classifies query as `simple | complex | ingest` (`router.py`).
- `planner_node` — emits a **DAG** of `PlanStep` objects with `id`, `tool_name`, `input_template`, `dependencies`, where templates may reference `{step_id}` (`planner.py`).
- `executor_node` — for `simple`/`ingest` runs **only `document_retrieval`** (hardwired); for `complex` walks the DAG in dependency order, substituting `{step_id}` placeholders. Catches only `ToolError` (`executor.py`).
- `evaluator_node` — LLM-scores retrieval quality, optionally reformulates the query, loops back to executor up to 3 times (`evaluator.py`).
- `synthesizer_node` — builds its answer from `reranked_chunks` **only**; `tool_results` is never read (`synthesizer.py`).

The graph is invoked from `backend/app/api/v1/chat.py` (`compiled_graph.ainvoke`) inside an SSE generator. Node code calls `emit_event(...)` (`stream_context.py`) to push SSE frames onto a per-request `asyncio.Queue`. The final state's `final_output`, `retrieved_chunks`, `rag_used`, and `relevance_score` are persisted as the assistant `Message` and used for citations.

### Audit findings (limitations motivating this redesign)
1. **Synthesizer ignores `tool_results` entirely.** Live financial data, web results, and comparator output are fetched, stored in state, then thrown away — only RAG chunks survive into the prompt.
2. **No inter-step data flow.** `financial_calculator` was meant to consume prior steps' numbers via `{step_id}` templating, but rendering produces a literal string like `compare revenue: {step_1} vs {step_2}` (the placeholder is never replaced with the prior result), so the calculator is dead.
3. **`ValidationError` crashes the executor node.** The executor catches `ToolError` per step but lets Pydantic `ValidationError` (raised by `input_cls.model_validate(...)`) propagate to the outer `except Exception`, which aborts the **entire** node and discards every other step's result.
4. **Simple path is hardwired to `document_retrieval`.** A simple question that needs a live stock price or a web lookup can never get one — the simple branch only ever runs RAG.
5. **Evaluator retry loop is broken and redundant.** It re-runs retrieval with a reformulated query but the reformulated `query` overwrites `state["query"]`, polluting downstream context; the `retry_count` accounting and the `score=1.0`-when-no-chunks workaround (see `[[feedback_evaluator_retry_loop]]`) exist only to defeat the loop. It adds latency and LLM cost for no measurable quality gain.
6. **Errors never surface to the user.** Node failures set `state["error"]`, but `chat.py` only emits an SSE `error` frame when `ainvoke` itself raises. A node that returns `{"error": ...}` and then continues to the synthesizer produces a silent, contextless answer.

### Prior decisions that constrain the design
- **State is a `TypedDict`** consumed via dict-key access; values must be JSON-serializable primitives (no Pydantic/ORM/UUID/datetime instances) per the contract in `state.py`.
- All LLM calls use `openai.AsyncOpenAI`; all logging uses `structlog`; all tool inputs are Pydantic models; SSE uses `emit_event` from `stream_context`; config is the `settings` singleton.
- `chat.py` and the frontend depend on the existing SSE event vocabulary (`node_update`, `tool_call`, `sources`, `token`, `error`, `done`) and on `final_output`/`retrieved_chunks`/`rag_used`/`relevance_score` in the final state. **These contracts are preserved.**
- Tool registry, tool input/output Pydantic schemas, and the `ToolError` hierarchy (`tools/base.py`) are unchanged except for removing `financial_calculator`.

### Locked design decisions (from the redesign brief)
1. Drop `financial_calculator` as a tool — the synthesizer (gpt-4o class) does all arithmetic from the data it is handed.
2. Drop the `evaluator` node entirely — the tool selector handles fallbacks up front.
3. All tools are **independent data-fetchers** — no inter-step dependencies, no DAG, no `{step_id}` templating.
4. The synthesizer receives **all** tool results as context, not just RAG chunks.
5. The executor catches **all** exceptions per step (`ToolError` + Pydantic `ValidationError` + bare `Exception`) and records the failure as data.
6. Simple path gets a new **Tool Selector** LLM node that picks the right single tool (or none).
7. Complex path: the planner produces a **flat, ordered list** of independent tool calls.
8. Tool failures surface to the synthesizer as context strings (`"tool X failed: reason"`).

### Decisions resolved during spec intake
- **Result format:** the executor serializes every tool output with `model_dump(mode="json")` into a normalized envelope `{tool_name, status, data | error}` so `tool_results` honors the state serialization contract.
- **Zero-tool path:** if the tool selector picks no tool, the run goes straight to the synthesizer for an LLM-only answer (preserving today's general-knowledge fallback).
- **Error boundary (two tiers):** per-tool failures become synthesizer context; fatal node-level errors set `state["error"]` **and** cause an SSE `error` frame so the user sees them.
- **Back-compat:** the `sources` event and `retrieved_chunks`/`rag_used`/`relevance_score` final-state keys are preserved.

## Scope

### In scope
- Rewrite `router_node` to classify into **`simple | complex | ingest`** only, with a sharpened prompt (no behavioral change for `ingest`; `simple` no longer implies "RAG only").
- New file `tool_selector.py` — an LLM node that, for `simple` queries, selects exactly **0 or 1** tool from the 5-tool registry and constructs its validated Pydantic input.
- Rewrite `planner_node` to emit a **flat ordered list** of independent tool calls (`tool_name` + structured `input`), with no `id`, no `dependencies`, no `{step_id}` templating.
- Rewrite `executor_node` to:
  - Execute the simple path's single selected tool **or** the complex path's flat list (sequentially or with bounded `asyncio.gather`, since steps are independent).
  - Catch `ToolError`, Pydantic `ValidationError`, and bare `Exception` **per step**, recording `{tool_name, status: "error", error: <reason>}` without aborting other steps.
  - Serialize successful outputs via `model_dump(mode="json")` into the normalized envelope.
  - Continue to populate `retrieved_chunks`/`reranked_chunks`/`rag_used` and emit the `sources` event for any `document_retrieval` results.
- Rewrite `synthesizer_node` to build its prompt from **all** of `tool_results` (rendered as labeled context blocks, including failure lines) **plus** RAG chunks, while keeping inline `[n]` citations for chunks and the no-documents guard.
- Rewrite `graph.py` to the new topology and delete the evaluator edges.
- Delete (or gut to a no-op passthrough) `evaluator.py` and remove it from the graph.
- Remove `financial_calculator` from `TOOL_REGISTRY` (`tools/__init__.py`) and from the planner/tool-selector prompts and the executor's `_TOOL_INPUT_MODELS` map. The file `tools/financial_calculator.py` and its schema may remain on disk but are unreferenced.
- Surface node-level `state["error"]` as an SSE `error` frame to the user (graph-level guard + `chat.py` post-run check).
- Add a `TOOL_SELECTOR_MODEL` setting to `app/config.py`.
- Update/replace agent unit and integration tests for the new topology.

### Out of scope
- **Re-ranking.** `reranked_chunks` stays equal to `retrieved_chunks` (the `# TODO: real reranker` remains); no reranker is added here.
- **Reformulation / retrieval-quality scoring.** Removed with the evaluator; not replaced.
- **The `ingest` path.** Router still emits `ingest` and the graph still exits to `END` after the router; document upload/ingestion behavior in `chat.py` is unchanged.
- **`document_finder` follow-up retrieval semantics** beyond what the executor already needs to feed chunks to the synthesizer; no new auto-ingest UX, no human-in-the-loop changes.
- **Frontend changes.** SSE contract is preserved, so no frontend work is required or included.
- **Multi-tool selection on the simple path.** Simple is strictly 0-or-1 tool; anything needing ≥2 tools must be classified `complex`.
- **Inter-step data dependencies / DAG execution / parallel fan-in.** Explicitly removed; tools are independent.
- **`financial_calculator` deletion from disk**, database schema changes, and new external integrations.
- **Caching of LLM or tool responses.**

## User flow

A financial analyst sends a message in the chat UI; `chat.py` opens the SSE stream and invokes the graph.

**Happy path — simple, single tool (e.g. "What is Apple's current stock price?")**
1. `router_node` emits `node_update{router, running}`, classifies `simple`.
2. `tool_selector_node` emits `node_update{tool_selector, running}`, selects `financial_data` with input `{ticker: "AAPL"}`, returns a one-element plan.
3. `executor_node` emits `tool_call{financial_data, running}` → runs the tool → `tool_call{financial_data, complete}`, stores the serialized envelope in `tool_results`.
4. `synthesizer_node` emits `node_update{synthesizer, running}`, builds a prompt containing the `financial_data` envelope, streams `token` frames, returns `final_output`.
5. `chat.py` persists the assistant message and emits `done`.

**Happy path — simple, zero tools (e.g. "Explain what a P/E ratio is")**
1. Router → `simple`.
2. Tool selector returns **no tool** (empty plan).
3. Executor is skipped (or runs with an empty plan and produces empty `tool_results`).
4. Synthesizer answers from model knowledge (LLM-only system prompt), streams tokens.

**Happy path — complex (e.g. "Compare AAPL, MSFT, GOOG revenue and the latest analyst sentiment")**
1. Router → `complex`.
2. `planner_node` emits a flat list, e.g. `[{company_comparator, {tickers:[…], metrics:["revenue"]}}, {web_search, {query:"…", search_type:"news"}}]`.
3. Executor runs each step independently (bounded concurrency), emitting `tool_call` running/complete per step; each result serialized into `tool_results` keyed by index/tool.
4. Synthesizer receives all envelopes (comparator + web results), does the comparison/arithmetic, streams the answer.

**Happy path — has_documents complex / RAG**
1. Router → `complex` (or `simple` for a single-retrieval question).
2. Planner includes a `document_retrieval` step (required first when `has_documents` is true); tool selector picks `document_retrieval` for the simple case.
3. Executor runs retrieval, normalizes chunks into `retrieved_chunks`/`reranked_chunks`, emits `sources`, also stores the retrieval envelope in `tool_results`.
4. Synthesizer uses the RAG system prompt, cites chunks `[1]…[n]`, and may also weave in any other tool envelopes.

**Edge / error states**
- **Tool selector picks an unknown/removed tool** → treated as "no tool"; logged `tool_selector_invalid_tool`; run proceeds LLM-only.
- **Tool selector cannot build valid input** (Pydantic `ValidationError`) → treated as "no tool" (or records a failure envelope); never crashes the node.
- **Planner returns malformed JSON / unknown tool / `financial_calculator`** → those steps are dropped (filtered against the registry); if the whole plan is empty, the run proceeds to synthesizer LLM-only.
- **A tool raises `ToolError`** → step recorded as `{status:"error", error: str(e)}`; `tool_call{…, error}` emitted; other steps unaffected; synthesizer told "tool X failed: reason".
- **A tool raises Pydantic `ValidationError` or any other `Exception`** → same handling as `ToolError` (caught per step, recorded, run continues).
- **All tools fail / return errors** → synthesizer receives only failure context and the query; it explains it could not retrieve the requested data and does not fabricate figures.
- **A node itself crashes** (LLM API error in router/tool-selector/planner/synthesizer) → node returns `{"error": str(e)}`; the graph routes to `END`; `chat.py` (or a graph-level guard) emits an SSE `error` frame with the message, and no assistant message is persisted.
- **No documents but query is document-specific** (existing keyword guard) → synthesizer returns "No documents uploaded yet…" without an LLM call.
- **`ingest` classification** → graph exits after router; `chat.py` emits `done` (unchanged).

## Detailed requirements

### Router
1. `router_node` MUST classify each query into exactly one of `{"simple", "complex", "ingest"}` and return `{"classification": <value>}`.
2. The router prompt MUST instruct: `simple` = answerable with **0 or 1** tool call (a single lookup, retrieval, or general-knowledge answer); `complex` = needs **2+** tool calls, comparisons across tickers, or fetching a new document; `ingest` = the user is adding a brand-new local file.
3. If the model returns a string outside the allowed set, the router MUST default to `"complex"` and log `router_unknown_classification` at WARNING.
4. The router MUST NOT change behavior for `ingest` (the graph still exits to `END` after the router for `ingest`).
5. On any exception, the router MUST return `{"error": str(e)}` and log `router_error` at ERROR.

### Tool selector (new)
6. `tool_selector_node` MUST run only for `classification == "simple"`.
7. It MUST call an LLM (model `settings.TOOL_SELECTOR_MODEL`) with `response_format={"type":"json_object"}`, temperature 0, and return JSON of shape `{"tool_name": <one of the 5 tools | null>, "input": <object> }`.
8. The selectable tools MUST be exactly: `document_retrieval`, `financial_data`, `web_search`, `company_comparator`, `document_finder`. `financial_calculator` MUST NOT appear.
9. If `tool_name` is `null`, an empty string, or not in the registry, the node MUST return an **empty plan** (`{"plan": []}`) and log `tool_selector_no_tool` (or `tool_selector_invalid_tool` for an unknown name) at DEBUG/WARNING respectively.
10. When `has_uploaded_documents` is true and the query references the documents, the selector SHOULD prefer `document_retrieval`; the prompt MUST state this bias.
11. The node MUST construct the selected tool's input by validating the LLM-provided `input` against the tool's Pydantic model, injecting `user_id`/`conversation_id` for `document_retrieval` and `document_finder`. It MUST emit a single-element `plan` in the **same flat shape the planner emits** (see #15) so the executor handles both paths uniformly.
12. If input validation fails (`ValidationError`), the node MUST fall back to an empty plan (LLM-only) rather than raising, and log `tool_selector_input_invalid` at WARNING.
13. On any unexpected exception, the node MUST return `{"error": str(e)}` and log `tool_selector_error` at ERROR.

### Planner
14. `planner_node` MUST run only for `classification == "complex"` and otherwise return `{"plan": []}`.
15. The planner MUST return JSON `{"steps": [ {"tool_name": <str>, "input": <object>}, … ]}` — a **flat, ordered list**. Steps MUST NOT contain `id`, `dependencies`, or `{step_id}` references. (The `PlanStep` TypedDict is redefined accordingly — see Data model changes.)
16. The planner prompt MUST list exactly the 5 tools (no `financial_calculator`) with their input shapes, and MUST state that all tools are independent data-fetchers with no shared inputs.
17. Steps whose `tool_name` is not in `TOOL_REGISTRY` MUST be dropped and logged `planner_invalid_tool` at WARNING.
18. When `has_uploaded_documents` is true, the plan MUST include a `document_retrieval` step (the prompt enforces "first if present"); ordering otherwise carries no execution semantics (steps are independent).
19. The planner MUST cap the plan at a maximum of **6** steps; extra steps are truncated and logged `planner_plan_truncated`.
20. On any exception, the planner MUST return `{"error": str(e)}` and log `planner_error` at ERROR. A parse failure that yields no usable steps MUST return `{"plan": []}` (not an error) so the run can still answer LLM-only.

### Executor
21. `executor_node` MUST handle a uniform flat `plan` for both simple (≤1 step) and complex (≤6 steps) paths; it MUST NOT special-case `classification` to hardwire `document_retrieval`.
22. For each step, the executor MUST: build the validated Pydantic input (re-validating, since selector/planner produced raw dicts), emit `tool_call{tool_name, step_id, running}`, invoke `TOOL_REGISTRY[tool_name]`, and on success emit `tool_call{…, complete}`.
23. Each step MUST be wrapped in `try/except (ToolError, ValidationError, Exception)`; a failure MUST: log `executor_tool_error` (WARNING for `ToolError`/`ValidationError`, ERROR for unexpected), emit `tool_call{…, error}`, and record `{"tool_name": …, "status": "error", "error": str(e)}` in `tool_results`. A single step failure MUST NOT abort the node or other steps.
24. Successful outputs MUST be stored as `{"tool_name": …, "status": "ok", "data": output.model_dump(mode="json")}` (or the equivalent JSON-serializable form for non-Pydantic returns) so `tool_results` contains only JSON-serializable values per the state contract.
25. `tool_results` MUST be keyed by a stable, unique step key (e.g. `"step_0"`, `"step_1"`, …) so duplicate tool names don't collide.
26. Independent steps MAY run concurrently via `asyncio.gather` with a bounded concurrency of **≤4**; ordering of the resulting list is not significant.
27. The executor MUST collect chunks from every `document_retrieval` envelope into `retrieved_chunks`, dedup them, set `reranked_chunks = retrieved_chunks`, set `rag_used = len(chunks) > 0`, and emit the `sources` event — preserving today's behavior.
28. The executor MUST preserve the existing `document_finder → immediate document_retrieval` follow-up so freshly ingested docs are queryable in the same turn, storing that retrieval's chunks the same way.
29. On an exception that escapes the per-step guard (i.e. in the node's own setup), the executor MUST return `{"error": str(e)}` and log `executor_error` at ERROR.

### Synthesizer
30. `synthesizer_node` MUST build its user prompt from: conversation summary, recent messages (last 3), analyst profile, the query, **all `tool_results` envelopes**, and the RAG chunks.
31. Each `tool_results` envelope MUST be rendered as a labeled block: successful results as `"<tool_name> result: <compact JSON or readable rendering>"`; failures as `"<tool_name> failed: <error>"`.
32. RAG chunks MUST continue to be rendered as numbered `[1]…[n]` blocks (max 10) and the system prompt MUST require inline `[n]` citations when chunks are present.
33. The synthesizer MUST pick its system prompt by context: RAG prompt when `reranked_chunks` is non-empty; an "tools + general knowledge" prompt when there are non-RAG tool results; the LLM-only prompt when there are neither.
34. The no-documents guard MUST be preserved: if there are no chunks **and** no successful tool results **and** the query is document-specific (existing keyword set), emit the "No documents uploaded yet…" message and skip the LLM call.
35. The synthesizer MUST stream tokens via `emit_event({"type":"token","token":delta})` and return `{"final_output": <full text>}`.
36. The synthesizer MUST NOT provide buy/sell/hold recommendations and MUST NOT fabricate figures absent from the provided tool data or chunks (existing rules retained).
37. The synthesizer MUST do all arithmetic itself (ratios, growth rates, comparisons) from the numbers present in `tool_results`; there is no calculator tool.
38. On any exception, the synthesizer MUST return `{"error": str(e)}` and log `synthesizer_error` at ERROR.

### Graph & error surfacing
39. `graph.py` MUST build the topology: entry `router_node`; conditional after router → `tool_selector_node` (simple), `planner_node` (complex), `END` (ingest); `tool_selector_node → executor_node`; `planner_node → executor_node`; `executor_node → synthesizer_node`; `synthesizer_node → END`. The `evaluator_node` and the `route_after_evaluator` edge MUST be removed.
40. A conditional edge MUST short-circuit to `END` (or to the synthesizer's error path) when a node returns a non-empty `state["error"]`, so a fatal node error does not silently flow into a degraded answer.
41. `chat.py` MUST, after `ainvoke`, check `final_state.get("error")`; if set and no `final_output`, it MUST emit `_sse("error", {"message": <error>})` and skip persistence. (Per-tool failures live in `tool_results`, not `state["error"]`, so they do not trigger this.)
42. The tool selector's single-element plan and the planner's multi-element plan MUST use the identical step schema so `executor_node` needs no path-specific branching beyond reading `state["plan"]`.

### Registry & config
43. `TOOL_REGISTRY` MUST NOT contain `financial_calculator`; `tools/__init__.py` `__all__` MUST be updated to drop stale exports (`FinancialCalculatorTool`, plus the already-dead `NewsFetchTool`/`SECFilingFetchTool` names if they are not real exports).
44. `executor.py` `_TOOL_INPUT_MODELS` MUST NOT contain `financial_calculator`.
45. `app/config.py` MUST define `TOOL_SELECTOR_MODEL` (default e.g. `"gpt-4o-mini"`); `EVALUATOR_MODEL` MAY remain defined but is unused.

### Observability requirements
46. Every node MUST emit `node_update{node, "running"}` on entry (existing pattern).
47. Each tool invocation MUST emit `tool_call{tool_name, step_id, status}` for `running`/`complete`/`error`.
48. Structured logs MUST include: `router_classified` (classification), `tool_selector_selected` (tool_name or "none"), `planner_plan_created` (step_count), `executor_completed` (step_count, chunk_count, error_count), `synthesizer_completed` (model, tool_result_count, chunk_count, output_length).

## Data model changes

**No database tables are created or modified.** The assistant `Message` persistence in `chat.py` (columns `content`, `rag_used`, `relevance_score`, `retrieved_chunk_ids`) is unchanged.

In-memory state-shape changes (TypedDicts in `backend/app/agent/state.py`):

### `PlanStep` (modified — breaking shape change)
| field | before | after | notes |
|---|---|---|---|
| `id` | `str` | **removed** | no step identity needed; steps are independent |
| `tool_name` | `str` | `str` | one of the 5 registry tools |
| `input_template` | `str` | **removed** | replaced by structured `input` |
| `input` | — | `dict[str, Any]` | structured, pre-validated tool input |
| `dependencies` | `list[str]` | **removed** | no inter-step dependencies |

New definition:
```python
class PlanStep(TypedDict):
    tool_name: str
    input: dict[str, Any]
```

### `tool_results` envelope (clarified shape)
`AgentState["tool_results"]` remains `dict[str, Any]` but each value MUST now be a JSON-serializable envelope:
```python
# success
{"tool_name": str, "status": "ok", "data": <json-serializable dict>}
# failure
{"tool_name": str, "status": "error", "error": str}
```
Keys are stable step keys (`"step_0"`, `"step_1"`, …). This replaces today's practice of storing raw Pydantic model instances, satisfying the state serialization contract in `state.py`.

### Fields deprecated but retained
`retrieval_quality_score`, `relevance_score`, `retry_count` remain in `AgentState` (still set in `initial_state` and read by `chat.py` for `relevance_score`), but are no longer mutated by an evaluator. `retry_count` is effectively dead and MAY be removed in a later cleanup once `chat.py`'s `initial_state` is updated in lockstep.

No indexes, foreign keys, or migrations are involved.

## API contracts

No HTTP endpoint signatures change. The agent runs entirely inside the existing **`POST /api/v1/chat`** SSE stream (auth: yes, Clerk bearer; behavior defined in `chat.py`). The contract that changes is the **internal SSE event vocabulary**, which is preserved 1:1:

| SSE event | data | when | change |
|---|---|---|---|
| `node_update` | `{node, status}` | node entry | now includes `tool_selector_node`; no longer includes `evaluator_node` |
| `tool_call` | `{tool_name, step_id, status}` | per tool run | `step_id` now `"step_0"…`; `financial_calculator` never appears |
| `sources` | `{chunks: [...]}` | after retrieval | unchanged |
| `token` | `{token}` | synthesizer streaming | unchanged |
| `error` | `{message}` | **now also** emitted when `final_state["error"]` is set | broadened per requirement #41 |
| `done` | `{message_id, conversation_id}` | end of run | unchanged |

Tool input contracts (validated by the executor; unchanged from existing schemas):
- `document_retrieval`: `{query:str(1..8000), user_id:UUID, conversation_id:UUID, top_k:1..20=5, ticker?, doc_type?, fiscal_year?}`
- `financial_data`: `{ticker: str /^[A-Z0-9]{1,10}$/}`
- `web_search`: `{query:str(≤500), search_type:"news"|"general"|"financial"="general", max_results:1..10=5}`
- `company_comparator`: `{tickers:list[str](1..10), metrics:list[str](≥1)}`
- `document_finder`: `{ticker:str, filing_type:"10-K"|"10-Q"|"transcript"|"presentation"|"other"="10-K", conversation_id:str, user_id?:str, query?:str(≤300)}`

No rate limiting is added at the agent layer (tool-level rate limiting via `tools/rate_limiter.py` is unchanged).

## Component and file structure

### Backend — modified
- `backend/app/agent/router.py` — rewrite the system prompt so `simple`/`complex` reflect the 0-or-1 vs 2+ tool distinction; behavior otherwise as today.
- `backend/app/agent/planner.py` — rewrite to emit a flat ordered list `{steps:[{tool_name, input}]}`; remove DAG/`{step_id}`/dependency logic; drop `financial_calculator`; cap at 6 steps.
- `backend/app/agent/executor.py` — rewrite to consume the uniform flat plan; per-step `try/except (ToolError, ValidationError, Exception)`; serialize outputs to envelopes; remove the hardwired simple-path retrieval; retain chunk collection/dedup, `sources` emission, and the `document_finder` retrieval follow-up; drop `financial_calculator` from `_TOOL_INPUT_MODELS`.
- `backend/app/agent/synthesizer.py` — rewrite to render all `tool_results` envelopes plus chunks; add the third "tools + general knowledge" system prompt; keep citations and the no-docs guard.
- `backend/app/agent/graph.py` — rewrite topology (add `tool_selector_node`, remove `evaluator_node`); add error short-circuit edge; update `route_after_router`.
- `backend/app/agent/state.py` — redefine `PlanStep` (flat `tool_name`+`input`); document the `tool_results` envelope shape.
- `backend/app/tools/__init__.py` — remove `FinancialCalculatorTool` from `TOOL_REGISTRY` and fix `__all__`.
- `backend/app/config.py` — add `TOOL_SELECTOR_MODEL`.
- `backend/app/api/v1/chat.py` — add the post-`ainvoke` `final_state["error"]` → SSE `error` check; remove `retry_count`/evaluator assumptions only if they break (otherwise leave `initial_state` intact).

### Backend — new
- `backend/app/agent/tool_selector.py` — new node `tool_selector_node`: LLM picks 0/1 tool, builds validated input, returns a one-element (or empty) flat `plan`.

### Backend — deleted / gutted
- `backend/app/agent/evaluator.py` — delete, or reduce to a documented no-op that is no longer imported by the graph. (Preferred: delete and remove the import.)
- `backend/app/tools/financial_calculator.py` and `backend/app/schemas/tools/financial_calculator.py` — remain on disk, unreferenced (per brief). Optional later cleanup.

### Tests
- `backend/tests/agent/test_graph.py` — update for the new topology (no evaluator; tool_selector present; simple→executor→synthesizer path).
- `backend/tests/agent/test_tool_selector.py` — **new**: selection of each tool, the zero-tool case, invalid-tool fallback, input-validation fallback.
- `backend/tests/agent/test_executor.py` — **new/updated**: per-step error isolation (ToolError, ValidationError, generic Exception), envelope serialization, chunk collection, `document_finder` follow-up.
- `backend/tests/agent/test_synthesizer.py` — **new/updated**: tool_results rendered into prompt, failure lines present, no-docs guard, citation behavior.
- `backend/tests/agent/test_planner.py` — **new/updated**: flat-list output, unknown-tool drop, `financial_calculator` rejected, 6-step cap.
- `backend/tests/test_agent_stream.py` / `test_contextvar_propagation.py` — adjust expected node/event sequences.

### Config / docs
- `docs/specs/agent-redesign.md` — this spec.

## External dependencies
- **OpenAI API (`openai.AsyncOpenAI`)** — powers router, tool selector, planner, synthesizer. If unavailable, the affected node raises, returns `{"error": …}`, and the user gets an SSE `error` frame (requirement #40/#41). Rate limits are OpenAI's; no agent-side retry is added.
- **yfinance** (via `financial_data`, `company_comparator`) — live/historical market data. If it fails or returns empty, the tool raises `ToolError`/`ToolValidationError`, captured as a per-step failure envelope; the synthesizer reports the data couldn't be fetched. No hard rate limit but subject to upstream throttling.
- **Serper** (via `web_search`) — current news/market data. Failure → per-step failure envelope. Subject to Serper plan rate limits.
- **SEC EDGAR / web fetch** (via `document_finder`) — fetch + ingest filings. Failure → per-step failure envelope; existing human-in-the-loop/confirmation flow in `chat.py` is unchanged.
- **pgvector / retrieval service** (via `document_retrieval`) — RAG search. Failure → per-step failure envelope and empty chunks; synthesizer falls back to its no-docs/LLM-only behavior.

## Testing plan

### Unit tests
- **router_node**: returns each of `simple|complex|ingest`; unknown output defaults to `complex` and logs WARNING; exception path returns `{"error":…}`.
- **tool_selector_node**: each of the 5 tools selected with valid input; `tool_name=null` → empty plan; unknown/removed tool (incl. `financial_calculator`) → empty plan; `ValidationError` on input → empty plan; `has_documents` biases to `document_retrieval`.
- **planner_node**: flat list emitted; steps with unknown tools dropped; `financial_calculator` dropped; >6 steps truncated; unparsable JSON → empty plan (not error); `has_documents` forces a `document_retrieval` step.
- **executor_node**: a step raising `ToolError`, a step raising `ValidationError`, and a step raising generic `Exception` are each captured as failure envelopes while a sibling success envelope is still produced; outputs serialized via `model_dump(mode="json")`; chunks collected/dedup'd; `sources` emitted; `document_finder` `ready/duplicate` triggers a follow-up retrieval; node setup exception → `{"error":…}`.
- **synthesizer_node**: prompt includes success envelopes and `"X failed: reason"` lines; RAG citation prompt used when chunks present; LLM-only prompt when neither; no-docs guard fires for document-specific queries with no data; arithmetic produced from provided numbers; `{"final_output":…}` returned and tokens streamed.

### Integration tests
- **Simple, single tool**: end-to-end through `compiled_graph.ainvoke` with a mocked tool; assert event order `node_update(router) → node_update(tool_selector) → tool_call(running/complete) → node_update(synthesizer) → token…` and a non-empty `final_output`.
- **Simple, zero tools**: tool selector returns none; assert no `tool_call` event and an LLM-only answer.
- **Complex, multi-tool**: planner returns 2–3 independent steps (e.g. comparator + web_search); assert all run, all envelopes present in the synthesizer prompt, and a single synthesized answer.
- **Tool failure isolation**: one of two complex steps raises; assert the other's data still reaches the synthesizer and the failure appears as context.
- **Node fatal error**: force the synthesizer LLM call to raise; assert `final_state["error"]` is set and `chat.py` emits an SSE `error` frame and persists no message.
- **RAG path**: `has_documents=true`, retrieval returns chunks; assert `sources` emitted, `retrieved_chunks`/`rag_used` populated, citations present.

### Manual verification
- Run the stack (`docker-compose up postgres redis`, backend `uvicorn`, frontend `npm run dev`); in the chat UI:
  1. Ask "What's NVDA's current price?" → confirm a `financial_data` tool call and a numeric answer.
  2. Ask "Compare AAPL/MSFT/GOOG revenue growth" → confirm comparator runs and the answer contains computed growth (proving synthesizer-side math, no calculator).
  3. With a temporarily broken Serper key, ask a news question → confirm the answer states the web lookup failed rather than crashing.
  4. Upload a 10-K, ask "Summarize the risk factors" → confirm `sources` panel populates and citations render.
  5. Force an OpenAI error (bad key) → confirm a visible error message in the UI, not a silent blank.

## Observability
- **Logging (structlog):** DEBUG for normal node completions (`router_classified`, `tool_selector_selected`, `planner_plan_created`, `executor_completed`, `synthesizer_completed`); WARNING for recoverable issues (`router_unknown_classification`, `planner_invalid_tool`, `planner_plan_truncated`, `tool_selector_invalid_tool`, `tool_selector_input_invalid`, `executor_tool_error` for `ToolError`/`ValidationError`); ERROR for node-fatal failures (`*_error`) and unexpected per-step exceptions.
- **SSE as live trace:** the `node_update` and `tool_call` frames are the user-facing/observability trace of which path (simple vs complex) ran and which tools fired with which status. LangSmith tracing in `chat.py` is unchanged.
- **Healthy state:** router emits exactly one classification; for simple, 0–1 `tool_call` pairs; for complex, 1–6 `tool_call` pairs; synthesizer streams ≥1 token; `done` emitted; `executor_completed.error_count == 0`.
- **Unhealthy state:** `error_count > 0` in `executor_completed` (degraded answer, still served), or a `*_error` log plus an SSE `error` frame (failed run, no message persisted). A run that reaches `synthesizer_completed` with `output_length == 0` and no no-docs guard is anomalous and worth alerting on.

## Risks and open questions
- **Synthesizer prompt bloat.** Passing all tool envelopes plus up to 10 chunks could exceed context or dilute focus for large `company_comparator`/`web_search` payloads. Mitigation: render compact JSON and cap per-tool payload size; revisit truncation limits after testing.
- **Synthesizer-side arithmetic accuracy.** Removing `financial_calculator` makes gpt-4o responsible for ratios/growth/DCF math; LLM arithmetic can be wrong on multi-step calculations. Mitigation: low temperature, explicit "show the numbers you used" prompting; monitor for math errors and reconsider a deterministic calculator if accuracy is poor.
- **`PlanStep` shape change is breaking.** Any code, test, or persisted artifact assuming `id`/`dependencies`/`input_template` will break. Mitigation: grep all `PlanStep` and `input_template` references (executor, tests, `chat.py`) and update in the same change.
- **`initial_state` coupling in `chat.py`.** It still sets `retry_count`/`retrieval_quality_score`; leaving them is safe but dead. Open question: remove now or in a follow-up cleanup. Deferred — leave intact to keep this change focused.
- **Concurrency vs upstream rate limits.** Running up to 4 independent tools concurrently could trip yfinance/Serper throttling on complex queries. Mitigation: bound concurrency (≤4) and rely on existing `tools/rate_limiter.py`; lower the bound if 429s appear.
- **Error short-circuit placement.** Routing a node `error` to `END` means a fatal synthesizer error yields no answer at all (vs. a degraded one). This is intentional (errors must be visible) but worth confirming against product expectations.
- **Assumption:** the frontend renders `tool_call` events generically and will not break when `step_id` changes from names like `"retrieval"` to `"step_0"`. If the UI keys off specific `step_id` values, it needs adjustment (believed not to, but unverified).
- **Assumption:** classifying "needs 2+ tools" as `complex` is reliable enough from the router prompt; borderline queries may land on `simple` and get an incomplete single-tool answer. Acceptable for v1; monitor misclassifications.
