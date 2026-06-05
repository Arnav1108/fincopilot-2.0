# Spec: Portfolio Chat

## Goal
Give an individual investor a dedicated, multi-turn chat surface scoped to a **single** portfolio, so they can have an ongoing conversation ("now compare that to my NVDA position", "what if I sold half my AAPL?") about that specific set of holdings instead of a one-shot, all-portfolios-aggregated analysis.

## Background
Today a user can already ask about their portfolio from the main chat:

- `PortfolioAnalysisTool` (`backend/app/tools/portfolio_analysis.py`) fetches **all** of a user's holdings across **all** portfolios (`Portfolio.user_id == user_uuid`), pulls live prices via yfinance with `Semaphore(4)`, and returns total value, per-holding gain/loss, and top gainers/losers.
- The main agent graph (`backend/app/agent/graph.py`: `router → {tool_selector | planner} → executor → synthesizer`) routes queries like *"How is my portfolio doing?"* to that tool, and the synthesizer can emit a `pie`/`bar` chart via `_extract_chart_data`.
- Portfolios are managed at `app/(shell)/portfolio/page.tsx` (a flat list of `PortfolioCard`s with `HoldingsTable` + add/delete dialogs) backed by `backend/app/api/v1/portfolios.py`.

Two limitations motivate this feature:

1. **No per-portfolio scoping.** `portfolio_analysis` aggregates every holding the user owns. With more than one portfolio this is confusing and wrong — there is no way to ask about "this portfolio".
2. **No multi-turn context in a portfolio context.** Main-chat conversations exist, but they are general-purpose and not tied to a portfolio. There is no surface where a conversation is anchored to a specific portfolio so the user can build up a line of reasoning about it.

Prior decisions that constrain the design:

- `Conversation`/`Message` already provide persistence, rolling summary (`rolling_summary`, regenerated every 6 messages), title auto-generation, and per-message `chart_data` (JSONB). These should be **reused**, not reinvented.
- `AgentState` must hold only JSON-serializable primitives (see the module docstring in `app/agent/state.py`).
- The SSE event protocol (`node_update`, `tool_call`, `token`, `sources`, `chart_data`, `done`, `error`) and the frontend `useStream` parser are established; the portfolio surface should speak the **same** protocol.
- Ownership is always enforced by `Portfolio.user_id == user.id` / `Conversation.user_id == user.id`.

## Scope

### In scope
- A new DB column `conversations.portfolio_id` (nullable FK → `portfolios.id`, `ON DELETE CASCADE`) that anchors a conversation to one portfolio. `NULL` = a normal main-chat conversation (unchanged behavior).
- A `portfolio_id` filter added to `PortfolioAnalysisInput` and `PortfolioAnalysisTool` so the tool can scope to one portfolio (back-compatible: omitted/`None` ⇒ existing all-portfolios behavior).
- A new lightweight **PortfolioAgent** LangGraph graph (separate from the main `compiled_graph`) that:
  1. Always grounds the turn by running `portfolio_analysis` for the scoped `portfolio_id`.
  2. Runs one lightweight planning step that may add 0+ follow-up tool calls restricted to `{financial_data, web_search, company_comparator}` over the portfolio's own tickers.
  3. Reuses the existing `executor_node` and `synthesizer_node` (including chart extraction).
- New backend endpoints under the portfolios router:
  - `POST /api/v1/portfolios/{portfolio_id}/conversations` — create a portfolio-scoped conversation.
  - `GET /api/v1/portfolios/{portfolio_id}/conversations` — list this portfolio's conversations (history sidebar).
  - `POST /api/v1/portfolios/{portfolio_id}/conversations/{conversation_id}/stream` — stream an agent answer via PortfolioAgent over SSE.
- Reuse of `GET /api/v1/conversations/{id}/messages` to load a portfolio conversation's history (already ownership-checked).
- A new frontend route `app/(shell)/portfolio/[id]/page.tsx`: two panes — **left** holdings table + summary stats (total value, holding count), **right** a chat panel with its own conversation-history list.
- Reuse of `MessageList`, `MessageBubble`, `InputBar`, `ChartBlock`, and `useStream` (parameterized for the new stream URL).
- New API client functions: `createPortfolioConversation`, `listPortfolioConversations`.
- Making each `PortfolioCard` on the existing portfolio index link to `/portfolio/[id]`.
- Reuse of title auto-generation, rolling summary, and memory extraction for portfolio conversations.

### Out of scope
- **Trade execution / order placement.** Read-only chat only.
- **Portfolio modification from chat** (add/remove/edit holdings via natural language). Holdings are still edited only through the existing portfolio dialogs/API. (Explicit future scope: "add this holding" actions.)
- **Real brokerage sync** (Robinhood, Schwab, Plaid, etc.).
- **Tax-lot accounting, wash-sale tracking, realized/unrealized cost-basis lots.**
- **Options, crypto, FX, or any non-equity holdings.**
- **Benchmarking against indices** (S&P 500 / sector comparison).
- **Scheduled alerts or notifications** about the portfolio.
- **Document upload / RAG inside portfolio chat.** Document ingestion and `document_retrieval`/`document_finder` stay in main chat only; the portfolio stream endpoint accepts no file uploads.
- **The `ingest` and `complex`-planner paths** of the main graph. PortfolioAgent does not classify `ingest` and does not use `planner_node`.
- **Multi-portfolio conversations.** A conversation is anchored to exactly one portfolio.
- **Showing portfolio conversations in the main chat sidebar** (and vice-versa). The two histories are kept separate.

## User flow

### Happy path
1. User opens `/portfolio` and clicks a portfolio card → navigates to `/portfolio/[id]`.
2. The page loads the portfolio (`GET /portfolios/{id}`) and renders the **left pane**: holdings table + summary stats. It loads the portfolio's conversations (`GET /portfolios/{id}/conversations`) for the **right pane** history list.
3. If no conversation is selected, the right pane shows an empty chat with the input bar. On first send, the client first calls `POST /portfolios/{id}/conversations` to create one, then streams to it. (Alternatively a "New conversation" button creates one up front.)
4. User types *"How is my portfolio allocated?"* and sends.
   - Client optimistically appends the user message, then `POST /portfolios/{id}/conversations/{cid}/stream`.
   - Server persists the user message, runs PortfolioAgent: `portfolio_analysis(portfolio_id)` grounds holdings → planner adds no follow-up → synthesizer streams text and emits a `pie` `chart_data` event.
   - Client renders streamed tokens and the pie chart via `ChartBlock`.
5. User asks a follow-up *"Which holding has the worst fundamentals?"*
   - PortfolioAgent grounds holdings again, planner adds `financial_data` steps for the portfolio's tickers, executor fetches them concurrently, synthesizer answers referencing the actual holdings.
6. User clicks **New conversation** → a fresh conversation starts; the previous one is saved and appears in the history list with an auto-generated title.
7. User clicks a previous conversation in the history list → `GET /conversations/{cid}/messages` loads the full message history (including any persisted `chart_data`).

### Edge cases and error states
- **Portfolio has no holdings.** `portfolio_analysis` returns `total_value=0, holdings=[]` with a `message`. PortfolioAgent skips follow-up tools and the synthesizer responds that the portfolio is empty and suggests adding holdings. No chart.
- **All price fetches fail.** `PortfolioAnalysisTool` raises `ToolError` ("Could not fetch prices …"). The executor records a failure envelope; the synthesizer acknowledges the failure and answers with whatever non-price data exists. SSE `tool_call` `status: "error"` is emitted; no fatal `error` frame.
- **Some tickers' prices fail.** Per-ticker `price_fetch_error` is surfaced in `HoldingResult`; the answer notes which holdings lacked prices. Allocation chart is computed from holdings that priced successfully.
- **Conversation not owned by user / wrong portfolio.** `404 Not Found` before any SSE frame (mirrors `stream_chat`).
- **Conversation belongs to a different portfolio than the path `portfolio_id`.** `404 Not Found`.
- **Portfolio not found / not owned.** `404 Not Found` on every portfolio-scoped endpoint.
- **Blank message.** `422` before streaming (mirrors `stream_chat`).
- **A follow-up tool fails (e.g., yfinance for one ticker).** Failure envelope only; the turn still completes. The synthesizer works with available data (existing behavior).
- **Graph node raises a fatal error with no output.** SSE `error` frame; no assistant message persisted (mirrors `_stream_events`).
- **User sends a non-portfolio question** (e.g., "what is a P/E ratio?"). Planner adds no follow-up tools; portfolio grounding still runs but the synthesizer answers from the holdings/general knowledge. (Portfolio context is always available but need not be used.)

## Detailed requirements

**Data scoping**
1. A `Conversation` MAY have a `portfolio_id`. When set, it MUST reference a `Portfolio` owned by the same `user_id`.
2. Deleting a `Portfolio` MUST cascade-delete its conversations (and their messages via the existing `messages.conversation_id` cascade).
3. `GET /portfolios/{id}/conversations` MUST return only non-deleted conversations whose `portfolio_id == id` AND `user_id == current_user.id`, ordered by `updated_at DESC`.
4. `portfolio_analysis` with a `portfolio_id` MUST return holdings for **only** that portfolio, and MUST verify the portfolio is owned by the requesting `user_id` (filter `Portfolio.user_id == user_uuid AND Portfolio.id == portfolio_id`). An unowned/unknown `portfolio_id` MUST yield the empty-holdings result, never another user's data.
5. `portfolio_analysis` with `portfolio_id = None` MUST behave exactly as today (all portfolios for the user) — no behavioral regression for main chat.

**Agent behavior**
6. PortfolioAgent MUST run `portfolio_analysis(user_id, portfolio_id)` exactly once per turn, before any follow-up tool selection, and make its result available to both the planner and the synthesizer.
7. The PortfolioAgent planner MUST only select follow-up tools from `{financial_data, web_search, company_comparator}` and MUST NOT select `document_retrieval`, `document_finder`, or `portfolio_analysis` (the latter already ran).
8. Follow-up `financial_data`/`company_comparator` tickers SHOULD be drawn from the portfolio's holdings; the planner MUST be given the holding tickers in its prompt.
9. The planner MUST be allowed to return zero follow-up steps (answerable from grounding alone).
10. The synthesizer MUST run unchanged: tool-results path → answer + `_extract_chart_data`. Allocation questions MUST be chartable as `pie`; per-ticker metric comparisons as `bar`.
11. PortfolioAgent MUST NOT emit `classification == "ingest"` and MUST NOT include `planner_node`/`router_node`/`tool_selector_node` from the main graph (it uses its own nodes).
12. `AgentState` MUST gain a `portfolio_id: str | None` field, seeded from the conversation's `portfolio_id`, kept JSON-serializable.

**Endpoints / streaming**
13. The stream endpoint MUST validate `(conversation_id, portfolio_id, user_id)` ownership and that the conversation's `portfolio_id` matches the path before opening the SSE stream; failures return `404`/`422` as normal HTTP responses (not SSE frames).
14. The stream endpoint MUST persist the user message before streaming and the assistant message (with `chart_data`) after, reusing the persistence/observability flow of `_stream_events` (Phases 3–6: relevance metadata, assistant message, memory extraction dispatch, LangSmith trace URL best-effort, rolling summary every 6 messages).
15. The stream endpoint MUST NOT accept file uploads (no `files` form field) and MUST NOT run the ingestion phase.
16. First user message in a portfolio conversation MUST trigger background title generation (reuse `_set_auto_title`).
17. SSE frames MUST use the existing event names and `_sse` formatting so the existing `useStream` parser works without protocol changes.

**Error handling**
18. Any tool failure inside a turn MUST be non-fatal (failure envelope), and the turn MUST still produce a synthesized answer when any data is available.
19. A fatal node error with empty `final_output` MUST emit an SSE `error` frame and skip assistant-message persistence.
20. Ownership/validation failures MUST never leak another user's or another portfolio's data.

**Frontend**
21. `/portfolio/[id]` MUST render a two-pane layout: left = holdings + summary stats; right = chat + conversation-history list for that portfolio.
22. The chat pane MUST reuse `MessageList`/`MessageBubble`/`InputBar`/`ChartBlock` and MUST render streamed tokens and `chart_data` identically to main chat.
23. The portfolio `InputBar` MUST NOT expose file upload (docs out of scope here).
24. Selecting a history conversation MUST load its full message history including persisted charts.
25. Each `PortfolioCard` on `/portfolio` MUST link to `/portfolio/[id]`.

**Performance**
26. Live-price latency is bounded by the existing `Semaphore(4)` + per-ticker `run_in_executor`; no new concurrency budget is introduced. Follow-up tools run under the executor's existing `Semaphore(4)`.
27. The portfolio grounding call and follow-up tools MUST NOT block the SSE stream from emitting `node_update`/`tool_call` progress events (reuse `emit_event`).

**Logging/observability**
28. Every new endpoint MUST log start/finish with `portfolio_id`, `conversation_id`, `user_id` (structlog), matching existing log-event naming (`*_started`, `*_completed`).
29. PortfolioAgent nodes MUST emit `node_update` and `tool_call` SSE events for each node/tool, consistent with the main graph.

## Data model changes

### Table: `conversations` (modified)
Add one column:

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `portfolio_id` | `UUID` | YES | `NULL` | FK → `portfolios.id`, `ON DELETE CASCADE`. `NULL` ⇒ main-chat conversation. |

- **Index:** `ix_conversations_portfolio_id` on `(portfolio_id)`. Justification: `GET /portfolios/{id}/conversations` filters by `portfolio_id` and the cascade delete scans by it; without the index both do sequential scans as conversation volume grows.
- **Foreign key:** `portfolio_id → portfolios.id`, `ondelete="CASCADE"`. Justification: deleting a portfolio must not leave orphaned, unreachable conversations; CASCADE matches the existing `user_id → users.id` cascade pattern on this table.
- **No change** to `messages` — it already cascades on `conversation_id`.

ORM change in `app/models/conversation.py`:
```python
portfolio_id: Mapped[Optional[uuid.UUID]] = mapped_column(
    UUID(as_uuid=True),
    sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
    nullable=True,
    index=True,
    default=None,
)
```
(Optional convenience relationship `Portfolio.conversations` with `cascade="all, delete-orphan"`.)

### Migration
- One Alembic migration, additive only: `add_portfolio_id_to_conversations`.
  - `op.add_column("conversations", sa.Column("portfolio_id", UUID(as_uuid=True), nullable=True))`
  - `op.create_index("ix_conversations_portfolio_id", "conversations", ["portfolio_id"])`
  - `op.create_foreign_key("fk_conversations_portfolio_id", "conversations", "portfolios", ["portfolio_id"], ["id"], ondelete="CASCADE")`
- Ordering: must run **after** the migration that created `portfolios` and **after** the latest existing head. No data backfill (existing rows keep `NULL`). Fully reversible (`drop_constraint` → `drop_index` → `drop_column`).

### Schema change: `PortfolioAnalysisInput` (`app/schemas/tools/portfolio_analysis.py`)
```python
class PortfolioAnalysisInput(BaseModel):
    user_id: str                       # injected from state["user_id"]
    portfolio_id: str | None = None    # injected from state["portfolio_id"]; None ⇒ all portfolios
```
`PortfolioAnalysisOutput` / `HoldingResult` are unchanged.

## API contracts

All endpoints require auth (`clerk_auth`) and operate as the authenticated user. Base prefix `/api/v1`.

### 1. `POST /portfolios/{portfolio_id}/conversations`
- **Auth:** yes (owner of `portfolio_id`).
- **Path params:** `portfolio_id: UUID`.
- **Request headers:** `Authorization: Bearer <jwt>`.
- **Request body:** none (mirrors `create_conversation`).
- **Success `201`:** `ConversationRead` (`{ id, title, created_at, updated_at }`) for a conversation with `portfolio_id` set and `title = "New Conversation"`.
- **Errors:** `404` portfolio not found/owned; `401` unauthenticated.

### 2. `GET /portfolios/{portfolio_id}/conversations`
- **Auth:** yes (owner).
- **Success `200`:** `ConversationRead[]`, non-deleted, `portfolio_id == path`, ordered `updated_at DESC`.
- **Errors:** `404` portfolio not found/owned.

### 3. `POST /portfolios/{portfolio_id}/conversations/{conversation_id}/stream`
- **Auth:** yes (owner of both portfolio and conversation; conversation's `portfolio_id` must equal path).
- **Request headers:** `Authorization: Bearer <jwt>`; `Content-Type: multipart/form-data`.
- **Request body (form):**
  - `message: str` — required, `max_length=10_000`, non-blank after strip.
  - `model: str` — optional, default `"gpt-4o"`.
  - *(No `files` field.)*
- **Response:** `text/event-stream; charset=utf-8`, headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
  - **SSE sequence:** `node_update` → (`tool_call`…)* → (`sources`)? → `token`* → (`chart_data`)? → `done`.
  - **`done` data:** `{ message_id: str|null, conversation_id: str }`.
  - **`error` data:** `{ message: str }` (and the existing optional `code`/`failed_files` shape on the frame, unused here).
- **Status codes:** `200` stream opened; `404` conversation/portfolio not found, not owned, or mismatched `portfolio_id`; `422` blank/oversize message; `401` unauthenticated.
- **Rate limiting:** none beyond existing infra.

### Reused: `GET /conversations/{conversation_id}/messages`
- Unchanged. Returns `MessageRead[]` (includes `chart_data`). Ownership already enforced. Used by the portfolio chat pane to load history.

## Component and file structure

### Backend
- **`app/models/conversation.py`** *(modified)* — add `portfolio_id` column (+ optional `Portfolio.conversations` relationship in `app/models/portfolio.py`).
- **`alembic/versions/xxxx_add_portfolio_id_to_conversations.py`** *(new)* — additive migration (column + index + FK).
- **`app/schemas/tools/portfolio_analysis.py`** *(modified)* — add `portfolio_id: str | None = None` to `PortfolioAnalysisInput`.
- **`app/tools/portfolio_analysis.py`** *(modified)* — when `input.portfolio_id` is set, add `Portfolio.id == portfolio_uuid` to the holdings query (keeping the `user_id` filter for ownership).
- **`app/agent/state.py`** *(modified)* — add `portfolio_id: str | None` to `AgentState`.
- **`app/agent/portfolio_graph.py`** *(new)* — defines `portfolio_compiled_graph`: `portfolio_context_node → portfolio_planner_node → executor_node → synthesizer_node`, with conditional edges to `END` on `error` (reuses `executor_node`, `synthesizer_node`).
- **`app/agent/portfolio_context.py`** *(new)* — `portfolio_context_node`: runs `portfolio_analysis(user_id, portfolio_id)`, writes a `step_portfolio` envelope into `tool_results` and a summary into `portfolio_data`; emits `node_update`/`tool_call`.
- **`app/agent/portfolio_planner.py`** *(new)* — `portfolio_planner_node`: LLM picks 0+ follow-up steps from `{financial_data, web_search, company_comparator}` over the holdings' tickers; appends to `plan`; validates inputs like the existing `tool_selector` (returns `{plan: [...]}`).
- **`app/api/v1/portfolios.py`** *(modified)* — add the three endpoints above, plus a `_stream_portfolio_events` generator (Phase-2..6 of `_stream_events`, no ingestion) invoking `portfolio_compiled_graph` with `portfolio_id` seeded. Factor shared persistence helpers from `chat.py` if practical; otherwise mirror them.
- **`app/agent/graph.py`** *(unchanged)* — main graph untouched.

### Frontend
- **`app/(shell)/portfolio/[id]/page.tsx`** *(new)* — two-pane portfolio chat page (left: holdings + stats; right: chat + history).
- **`components/portfolio/PortfolioChatPane.tsx`** *(new)* — wires `useStream` (new URL), `MessageList`, `InputBar` (no upload), streaming state — modeled on `app/(shell)/chat/[id]/page.tsx`.
- **`components/portfolio/PortfolioConversationList.tsx`** *(new)* — history list for the portfolio (select / new conversation).
- **`hooks/useStream.ts`** *(modified)* — accept a configurable stream path (e.g. an optional `endpoint` builder) so it can target `/portfolios/{pid}/conversations/{cid}/stream`; default keeps current `/conversations/{cid}/stream`.
- **`lib/api.ts`** *(modified)* — add `createPortfolioConversation(token, portfolioId)` and `listPortfolioConversations(token, portfolioId)`.
- **`lib/types.ts`** *(modified, if needed)* — `ConversationRead` may gain optional `portfolio_id?: string | null` (additive).
- **`app/(shell)/portfolio/page.tsx`** *(modified)* — make each `PortfolioCard` (or its name) link to `/portfolio/[id]`.

### Tests
- **`backend/tests/test_portfolio_analysis_tool.py`** *(modified/new)* — `portfolio_id` scoping + ownership.
- **`backend/tests/test_portfolio_chat_api.py`** *(new)* — endpoint ownership/validation + stream happy path.
- **`backend/tests/test_portfolio_graph.py`** *(new)* — grounding always runs; planner tool restriction; empty-portfolio path.
- **`frontend`** — component/route smoke test for `/portfolio/[id]` if a test setup exists.

### Config
- No new env vars. (PortfolioAgent reuses `ROUTER_MODEL`/`TOOL_SELECTOR_MODEL`/`SYNTHESIZER_MODEL` settings as appropriate.)

## External dependencies
- **yfinance** — live prices for grounding + `financial_data`. Already used. Unavailability ⇒ `ToolError` / per-ticker `price_fetch_error`, handled non-fatally (req 18, edge cases). Informal rate limits; mitigated by `Semaphore(4)`.
- **Serper (web_search)** — only if the planner selects `web_search`. Already integrated. Unavailability ⇒ failure envelope; turn still answers.
- **OpenAI** — planner + synthesizer + chart extraction, via the existing `openai_client` singleton. Unavailability ⇒ node error → SSE `error` frame.
- **Redis / Celery** — reused for memory-extraction dispatch and (indirectly) rolling summary. Best-effort; dispatch failure is logged, not fatal.
- No **new** third-party services.

## Testing plan

### Unit tests
- `PortfolioAnalysisTool`:
  - `portfolio_id` set ⇒ returns only that portfolio's holdings.
  - `portfolio_id` for a portfolio owned by another user ⇒ empty result (no leak).
  - `portfolio_id = None` ⇒ unchanged all-portfolios behavior (regression guard).
- `portfolio_planner_node`:
  - Allocation query ⇒ zero follow-up steps.
  - "worst fundamentals" query ⇒ `financial_data` steps for holding tickers only.
  - Never selects `document_retrieval`/`document_finder`/`portfolio_analysis`.
  - Empty portfolio ⇒ no follow-up steps.
- `portfolio_context_node`: always produces a `step_portfolio` envelope and seeds `portfolio_data`; injects `portfolio_id` from state.
- Migration: upgrade adds column/index/FK; downgrade removes them.

### Integration tests
- End-to-end stream over `POST /portfolios/{pid}/conversations/{cid}/stream`:
  - Allocation question ⇒ `token` stream + a `pie` `chart_data` + persisted assistant message with `chart_data`.
  - Follow-up question in the **same** conversation ⇒ prior turn present in `recent_messages`/summary context (multi-turn verified).
  - Ownership: another user's conversation/portfolio ⇒ `404`; mismatched `portfolio_id` ⇒ `404`; blank message ⇒ `422`.
  - Empty portfolio ⇒ graceful "no holdings" answer, no chart, `200`.
- `POST`/`GET /portfolios/{pid}/conversations` ⇒ create returns `portfolio_id`-scoped conversation; list returns only that portfolio's conversations ordered by `updated_at`.
- Cascade: delete portfolio ⇒ its conversations (and messages) are gone.

### Manual verification (DoD scenario)
1. Go to `/portfolio/[id]` → holdings table + stats on the left, chat on the right.
2. Ask *"How is my portfolio allocated?"* → pie chart renders.
3. Ask *"Which holding has the worst fundamentals?"* → uses `financial_data`, gives a specific answer about actual holdings.
4. Click **New conversation** → previous one is saved and visible in the history list (with auto-title).
5. Reopen the previous conversation → full message history (and any charts) loads.
6. Confirm a second user cannot open this portfolio's chat (`404`).

## Observability
- **Logs (structlog):**
  - `portfolio_conversation_created` (`portfolio_id`, `conversation_id`, `user_id`) — INFO.
  - `portfolio_chat_stream_started` / `portfolio_chat_stream_completed` (`portfolio_id`, `conversation_id`, `user_id`, `assistant_message_id`, `chart_type`) — INFO.
  - `portfolio_analysis` `tool_called` / `tool_succeeded` (existing DEBUG) now include `portfolio_id`.
  - `portfolio_price_fetch_failed` / `portfolio_all_prices_failed` (existing WARNING/ERROR).
  - Planner: `portfolio_planner_selected` (DEBUG, step count), `portfolio_planner_error` (ERROR).
  - Node errors reuse existing `*_error` events.
- **SSE telemetry:** `node_update` per node and `tool_call` per tool (running/complete/error) — drives the frontend `AgentStatus`.
- **Traces:** LangSmith run URL persisted to the assistant message's `agent_trace` (best-effort, reused).
- **Healthy state:** stream emits `node_update(portfolio_context) → tool_call(portfolio_analysis, complete) → [tool_call(...)] → token* → [chart_data] → done`, and a `messages` row is persisted.
- **Unhealthy state:** a `tool_call … error` with the turn still completing (degraded), or a terminal `error` frame with no persisted assistant message (failed turn).

## Risks and open questions
- **Multi-tool planning without the main planner.** Reusing only a single `portfolio_planner_node` (no iterative planner) may underperform on genuinely multi-step asks (e.g., "compare fundamentals of my two worst performers, then explain why"). Mitigation: grounding always provides holdings; planner may emit several parallel `financial_data` steps in one pass. Deferred: an iterative loop if single-pass proves insufficient.
- **yfinance latency/limits compound.** Grounding fetches N holding prices, then `financial_data` may refetch per ticker. Risk of slow turns / rate-limiting for large portfolios. Mitigation: reuse `Semaphore(4)`; consider caching grounding prices in state so `financial_data` can reuse them (deferred optimization).
- **`useStream` parameterization.** Changing the hook's URL contract touches main chat; must keep the default path so existing chat is unaffected (regression risk). Verify `chat/[id]` still streams unchanged.
- **History separation.** Portfolio conversations must not appear in the main sidebar (`GET /conversations/` currently returns all of a user's non-deleted conversations — it would now include portfolio ones). **Open question / decision needed:** should `GET /conversations/` exclude rows where `portfolio_id IS NOT NULL`? Assumed **yes** (keep main sidebar clean); this is a one-line filter but is a behavioral change to an existing endpoint — flagging for confirmation.
- **Chart correctness for allocation.** `_extract_chart_data` is LLM-driven; it must reliably produce a `pie` for allocation. If unreliable, consider deterministically constructing the allocation chart in `portfolio_context_node` from `HoldingResult.current_value`. Deferred.
- **Assumption:** the existing rolling-summary/memory-extraction machinery is appropriate for portfolio conversations as-is. If portfolio chats should feed cross-session memory differently, revisit.
```

