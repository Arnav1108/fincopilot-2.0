# Spec: Portfolio Tracking

## Goal
Allow financial analysts and investors to persist a portfolio of stock holdings in the database and ask natural language questions about portfolio performance without re-entering ticker data each session.

## Background
Today users can ask about individual tickers or compare companies using `financial_data` and `company_comparator`, but there is no concept of a personal portfolio. Every session starts fresh — the agent has no memory of what the user owns. Users must paste tickers into each query, which is friction for recurring portfolio-level questions ("how's my portfolio doing?", "which holdings are underperforming?").

The agent already supports five tools registered in `TOOL_REGISTRY` (`backend/app/tools/__init__.py`). yfinance is already a dependency and is used in `financial_data.py` and `company_comparator.py` (the `run_in_executor` + `asyncio.gather` concurrency pattern is established there). SQLAlchemy 2.0 async models exist in `backend/app/models/`. Alembic migrations run up to `0007`. The agent's `AgentState` TypedDict lives in `backend/app/agent/state.py` and must remain JSON-serializable.

Prior constraints that shape this design:
- All AgentState values must be plain JSON-serializable Python primitives (documented in `state.py` line 9).
- Tool input models must be registered in `_TOOL_INPUT_MODELS` in both `executor.py` and `tool_selector.py`.
- All endpoints live under the `/api` prefix and use the `clerk_auth` dependency.
- structlog everywhere — no `print()` or stdlib `logging`.
- Settings singleton at `app.config.settings` — no new env vars.

## Scope

### In scope
- `Portfolio` and `PortfolioHolding` SQLAlchemy models with Alembic migration `0008`.
- REST CRUD endpoints under `/api/v1/portfolios`:
  - `POST /portfolios` — create a portfolio (name)
  - `GET /portfolios` — list user's portfolios
  - `GET /portfolios/{portfolio_id}` — get a single portfolio with holdings
  - `DELETE /portfolios/{portfolio_id}` — delete a portfolio (cascades to holdings)
  - `POST /portfolios/{portfolio_id}/holdings` — add a holding (ticker + shares + optional avg_cost_basis)
  - `GET /portfolios/{portfolio_id}/holdings` — list holdings for a portfolio
  - `DELETE /portfolios/{portfolio_id}/holdings/{holding_id}` — remove a holding
- `portfolio_analysis` agent tool (6th tool) that:
  - Reads all of the authenticated user's portfolios and holdings from the DB
  - Fetches current prices via yfinance in parallel (≤4 concurrent, same pattern as `company_comparator.py`)
  - Returns total portfolio value, per-holding breakdown, top gainers and losers
- Registration of `portfolio_analysis` tool in `TOOL_REGISTRY`, `_TOOL_INPUT_MODELS` (executor + tool_selector), and both LLM system prompts (planner + tool_selector).
- `portfolio_data: dict | None` field added to `AgentState` (with `None` default).
- Router system prompt updated with portfolio-question examples so router correctly classifies portfolio queries as `simple`.
- Pydantic schemas for tool I/O and for REST API request/response bodies.
- structlog instrumentation on every new code path.
- Unit tests for tool logic and API endpoints.

### Out of scope
- Frontend portfolio management UI (API only).
- Historical performance tracking or time-series price storage.
- Portfolio benchmarking against indices (S&P 500, etc.).
- Dividend tracking.
- Multi-currency support (all values in USD as returned by yfinance).
- Portfolio sharing between users.
- PDF/CSV export of portfolio data.
- Chart generation for portfolio.
- Per-portfolio agent queries (agent tool always aggregates ALL portfolios for the user into one view).
- Real-time price streaming or WebSocket updates for portfolio prices.

## User flow

### Happy path — CRUD
1. User sends `POST /api/v1/portfolios` with `{"name": "Tech Portfolio"}`.
2. System creates a portfolio row owned by `user_id` from JWT, returns the portfolio object with a UUID.
3. User sends `POST /api/v1/portfolios/{id}/holdings` with `{"ticker": "AAPL", "shares": 10, "avg_cost_basis": 150.00}`.
4. System validates ticker format (uppercase letters/digits, 1–10 chars), creates holding row, returns holding object.
5. User sends `GET /api/v1/portfolios` — receives list of their portfolios.
6. User sends `GET /api/v1/portfolios/{id}/holdings` — receives list of holdings for that portfolio.
7. User sends `DELETE /api/v1/portfolios/{id}/holdings/{holding_id}` — holding is deleted.
8. User sends `DELETE /api/v1/portfolios/{id}` — portfolio and all its holdings are deleted (CASCADE).

### Happy path — agent query
1. User types "How's my portfolio doing?" in chat.
2. Router classifies as `simple`.
3. Tool selector selects `portfolio_analysis` with input `{}` (user_id injected automatically by executor).
4. Executor calls `PortfolioAnalysisTool.__call__(input)` which:
   a. Opens a DB session, queries all portfolios + holdings for `user_id`.
   b. If no holdings found, returns output with `total_value=0`, empty lists, and a `message` explaining no holdings are configured.
   c. For each unique ticker in holdings, spawns a `run_in_executor` yfinance price fetch (bounded ≤4 concurrent via `asyncio.Semaphore(4)`).
   d. Computes per-holding current value, gain/loss (if `avg_cost_basis` present), total portfolio value.
   e. Sorts by gain/loss % descending to populate `top_gainers` and `top_losers`.
   f. Returns `PortfolioAnalysisOutput`.
5. Synthesizer receives tool result in `tool_results` and produces a natural language answer with specific numbers.

### Edge cases and error states
- **No portfolios**: tool returns `PortfolioAnalysisOutput(total_value=0.0, holdings=[], top_gainers=[], top_losers=[], message="No portfolio holdings found. Add holdings via the portfolio API.")`. Synthesizer answers accordingly.
- **Invalid ticker in DB**: yfinance returns `None` for price. Holding is included in output with `current_price=null`, `current_value=null`, and a non-null `price_fetch_error` field. Other holdings are still returned.
- **All tickers fail**: `ToolError` raised — executor records error envelope, synthesizer notifies user that prices could not be fetched.
- **Portfolio not found on DELETE/GET**: `404 Not Found`.
- **Portfolio owned by different user**: query always filters by `user_id` from JWT — returns 404 as if it doesn't exist (no information leakage).
- **Duplicate holding for same ticker in same portfolio**: allowed — user may have bought at different times. Shares are summed by the tool at analysis time.
- **ticker validation on holding creation**: regex `^[A-Z0-9]{1,10}$` enforced at Pydantic layer. Returns `422 Unprocessable Entity` on failure.
- **avg_cost_basis ≤ 0**: rejected with `422`. Must be positive if provided.
- **shares ≤ 0**: rejected with `422`. Must be positive.

## Detailed requirements

### Functional
1. `POST /api/v1/portfolios` must create a portfolio with a server-generated UUID, owner set to the authenticated `user_id`, `created_at` and `updated_at` set to now, and return `201 Created`.
2. `GET /api/v1/portfolios` must return only portfolios owned by the authenticated user, ordered by `created_at DESC`.
3. `GET /api/v1/portfolios/{portfolio_id}` must return the portfolio with its holdings list. Returns `404` if not found or not owned by the requesting user.
4. `DELETE /api/v1/portfolios/{portfolio_id}` must delete the portfolio and cascade-delete all its holdings atomically. Returns `204 No Content`. Returns `404` if not found or not owned.
5. `POST /api/v1/portfolios/{portfolio_id}/holdings` must validate that the ticker matches `^[A-Z0-9]{1,10}$`, that `shares > 0`, and that `avg_cost_basis > 0` if provided. Returns `422` on validation failure, `404` if portfolio not found/not owned.
6. `GET /api/v1/portfolios/{portfolio_id}/holdings` must return holdings ordered by `created_at ASC`. Returns `404` if portfolio not found/not owned.
7. `DELETE /api/v1/portfolios/{portfolio_id}/holdings/{holding_id}` must delete only the specified holding. Returns `404` if either the portfolio or the holding is not found, or if the holding does not belong to the portfolio.
8. `portfolio_analysis` tool must accept `PortfolioAnalysisInput(user_id: str)` and return `PortfolioAnalysisOutput`.
9. `portfolio_analysis` tool must fetch current prices from yfinance using `loop.run_in_executor(None, ...)` wrapped in `asyncio.gather` with `asyncio.Semaphore(4)` — identical to the pattern in `company_comparator.py`.
10. Per-holding output must include: `ticker`, `shares`, `current_price` (nullable), `current_value` (nullable), `avg_cost_basis` (nullable), `gain_loss` (nullable, in dollars), `gain_loss_pct` (nullable, as a fraction e.g. 0.12 = 12%), `price_fetch_error` (nullable string).
11. `top_gainers` and `top_losers` in the output must each be a list of up to 3 holding tickers sorted by `gain_loss_pct` descending and ascending respectively. Only holdings with a non-null `gain_loss_pct` are eligible.
12. `portfolio_analysis` must be registered in `TOOL_REGISTRY` in `backend/app/tools/__init__.py`.
13. `PortfolioAnalysisInput` must be registered in `_TOOL_INPUT_MODELS` in both `executor.py` and `tool_selector.py`.
14. `executor.py` must inject `user_id` from `state["user_id"]` into the tool input for `portfolio_analysis` (same pattern as `document_retrieval`).
15. `AgentState` TypedDict must gain a `portfolio_data: dict | None` field with a default of `None`.
16. The planner system prompt must list `portfolio_analysis` as an available tool with its input schema and usage description.
17. The tool_selector system prompt must list `portfolio_analysis` and include examples that route portfolio questions to it.
18. The router system prompt must include portfolio question examples classified as `simple`.

### Security
19. Every endpoint must use `clerk_auth` as a FastAPI dependency. Requests without a valid Clerk JWT must receive `401 Unauthorized`.
20. All DB queries for portfolios must filter by `user_id` derived from the JWT. A user must never be able to read or modify another user's portfolio data.
21. The `portfolio_analysis` tool uses `state["user_id"]` (injected by the agent graph from the Clerk JWT) — it must never accept a user_id from the LLM-generated plan input.

### Performance
22. yfinance price fetches in `portfolio_analysis` must run with concurrency bounded to ≤4 simultaneous calls (Semaphore(4)).
23. The `portfolio_holdings` table must have an index on `(portfolio_id)` to support efficient holding lookups per portfolio.
24. The `portfolios` table must have an index on `(user_id)` to support efficient per-user portfolio listing.

### Logging / Observability
25. Every tool call must log `tool_called` at DEBUG level with `tool_name`, `user_id`, and `holding_count` (after DB fetch).
26. Every yfinance failure for an individual ticker must log at WARNING level with `ticker` and `error`.
27. Successful tool completion must log `tool_succeeded` at DEBUG level with `tool_name`, `total_value`, and `holding_count`.
28. Every API endpoint must log at DEBUG level on success and WARNING level on 4xx errors using structlog bound with `user_id` and relevant IDs.

## Data model changes

### Table: `portfolios`
```sql
CREATE TABLE portfolios (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_portfolios_user_id ON portfolios (user_id);
```
- `user_id` index: supports `WHERE user_id = $1` in `GET /portfolios` and ownership checks — without it, every list query is a full table scan.
- `ON DELETE CASCADE` on `users.id`: ensures portfolios are cleaned up if a user is deleted.

### Table: `portfolio_holdings`
```sql
CREATE TABLE portfolio_holdings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    portfolio_id    UUID NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL,
    shares          NUMERIC(18, 6) NOT NULL CHECK (shares > 0),
    avg_cost_basis  NUMERIC(18, 6) CHECK (avg_cost_basis > 0),  -- nullable
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_portfolio_holdings_portfolio_id ON portfolio_holdings (portfolio_id);
```
- `portfolio_id` index: supports `WHERE portfolio_id = $1` for listing/fetching holdings — the most common access pattern.
- `ON DELETE CASCADE` on `portfolios.id`: holdings are deleted when the parent portfolio is deleted.
- `NUMERIC(18, 6)` for `shares` and `avg_cost_basis`: avoids float precision loss for financial quantities.
- `avg_cost_basis` is nullable: user may not know their cost basis.

### SQLAlchemy models (`backend/app/models/portfolio.py` — new file)
```python
class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]  # FK → users.id CASCADE
    name: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    holdings: Mapped[list["PortfolioHolding"]] = relationship(...)

class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"
    id: Mapped[uuid.UUID]
    portfolio_id: Mapped[uuid.UUID]  # FK → portfolios.id CASCADE
    ticker: Mapped[str]
    shares: Mapped[Decimal]
    avg_cost_basis: Mapped[Optional[Decimal]]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    portfolio: Mapped["Portfolio"] = relationship(...)
```

### Migration
- File: `backend/alembic/versions/0008_portfolio_tables.py`
- `down_revision = "0007"`
- `upgrade()`: creates `portfolios`, then `portfolio_holdings`, then both indexes.
- `downgrade()`: drops `portfolio_holdings`, then `portfolios` (cascade handles FK).

### `backend/app/models/__init__.py`
- Add import of `Portfolio` and `PortfolioHolding` so Alembic autogenerate sees them.

## API contracts

### `POST /api/v1/portfolios`
- **Auth**: Clerk JWT required (`clerk_auth` dependency).
- **Request body**:
  ```json
  { "name": "string (1–200 chars, required)" }
  ```
- **Response 201**:
  ```json
  {
    "id": "uuid",
    "user_id": "uuid",
    "name": "string",
    "created_at": "ISO8601",
    "updated_at": "ISO8601",
    "holdings": []
  }
  ```
- **Response 422**: Pydantic validation error (name empty or too long).
- **Response 401**: Missing or invalid JWT.

### `GET /api/v1/portfolios`
- **Auth**: Clerk JWT required.
- **Response 200**:
  ```json
  [
    {
      "id": "uuid",
      "user_id": "uuid",
      "name": "string",
      "created_at": "ISO8601",
      "updated_at": "ISO8601",
      "holdings": [...]
    }
  ]
  ```
- Returns empty array `[]` if user has no portfolios.

### `GET /api/v1/portfolios/{portfolio_id}`
- **Auth**: Clerk JWT required.
- **Response 200**: Same shape as single item above, with full holdings list.
- **Response 404**: Portfolio not found or not owned by user.

### `DELETE /api/v1/portfolios/{portfolio_id}`
- **Auth**: Clerk JWT required.
- **Response 204**: No body.
- **Response 404**: Portfolio not found or not owned by user.

### `POST /api/v1/portfolios/{portfolio_id}/holdings`
- **Auth**: Clerk JWT required.
- **Request body**:
  ```json
  {
    "ticker": "string (uppercase A-Z0-9, 1–10 chars, required)",
    "shares": "number > 0 (required)",
    "avg_cost_basis": "number > 0 (optional, nullable)"
  }
  ```
- **Response 201**:
  ```json
  {
    "id": "uuid",
    "portfolio_id": "uuid",
    "ticker": "string",
    "shares": "number",
    "avg_cost_basis": "number | null",
    "created_at": "ISO8601",
    "updated_at": "ISO8601"
  }
  ```
- **Response 404**: Portfolio not found or not owned by user.
- **Response 422**: Ticker format invalid, shares ≤ 0, or avg_cost_basis ≤ 0.

### `GET /api/v1/portfolios/{portfolio_id}/holdings`
- **Auth**: Clerk JWT required.
- **Response 200**: Array of holding objects (same shape as 201 response above).
- **Response 404**: Portfolio not found or not owned by user.

### `DELETE /api/v1/portfolios/{portfolio_id}/holdings/{holding_id}`
- **Auth**: Clerk JWT required.
- **Response 204**: No body.
- **Response 404**: Portfolio not found/not owned, or holding not found/not in this portfolio.

## Component and file structure

### Backend — new files
| File | Purpose |
|---|---|
| `backend/app/models/portfolio.py` | `Portfolio` and `PortfolioHolding` SQLAlchemy models. |
| `backend/app/schemas/portfolio.py` | Pydantic request/response schemas for the REST API (`PortfolioCreate`, `PortfolioResponse`, `HoldingCreate`, `HoldingResponse`). |
| `backend/app/schemas/tools/portfolio_analysis.py` | Pydantic tool I/O schemas (`PortfolioAnalysisInput`, `HoldingResult`, `PortfolioAnalysisOutput`). |
| `backend/app/tools/portfolio_analysis.py` | `PortfolioAnalysisTool` — DB fetch + yfinance price fetch + output assembly. |
| `backend/app/api/v1/portfolios.py` | FastAPI router with all 7 CRUD endpoints. |
| `backend/alembic/versions/0008_portfolio_tables.py` | Alembic migration creating `portfolios` and `portfolio_holdings` tables with indexes. |

### Backend — modified files
| File | Change |
|---|---|
| `backend/app/models/__init__.py` | Import `Portfolio`, `PortfolioHolding` so Alembic sees them. |
| `backend/app/tools/__init__.py` | Import `PortfolioAnalysisTool`, add `"portfolio_analysis"` to `TOOL_REGISTRY`. |
| `backend/app/api/v1/router.py` | Include `portfolios.router` under `/portfolios` prefix. |
| `backend/app/agent/state.py` | Add `portfolio_data: dict | None` to `AgentState` TypedDict. |
| `backend/app/agent/executor.py` | Import `PortfolioAnalysisInput`, add to `_TOOL_INPUT_MODELS`, inject `user_id` for `portfolio_analysis`. |
| `backend/app/agent/tool_selector.py` | Import `PortfolioAnalysisInput`, add to `_TOOL_INPUT_MODELS`, add `portfolio_analysis` to `_SYSTEM_PROMPT`. |
| `backend/app/agent/planner.py` | Add `portfolio_analysis` tool description to `_SYSTEM_PROMPT`. |
| `backend/app/agent/router.py` | Add portfolio query examples to `_SYSTEM_PROMPT` (classified as `simple`). |

### Tests — new files
| File | Purpose |
|---|---|
| `backend/tests/test_portfolio_api.py` | Integration tests for all 7 REST endpoints using `AsyncClient`. |
| `backend/tests/test_portfolio_analysis_tool.py` | Unit tests for `PortfolioAnalysisTool` with mocked DB session and mocked yfinance. |

## External dependencies

| Dependency | Role | Unavailability | Rate limits |
|---|---|---|---|
| `yfinance` | Current stock price via `Ticker.fast_info.last_price` | Individual ticker returns `None`; tool logs WARNING and includes `price_fetch_error` in holding output. If all tickers fail, tool raises `ToolError`. | No documented rate limit; concurrency capped at 4 as a courtesy. |

No new packages required. yfinance is already installed.

## Testing plan

### Unit tests (`test_portfolio_analysis_tool.py`)
- `test_returns_empty_output_when_no_holdings`: mock DB returns empty holdings list → `total_value == 0`, `holdings == []`, non-empty `message`.
- `test_single_holding_with_cost_basis`: mock DB returns one holding (AAPL, 10 shares, cost_basis=$150), mock yfinance returns price=$170 → `total_value == 1700`, `gain_loss == 200`, `gain_loss_pct ≈ 0.1333`.
- `test_single_holding_no_cost_basis`: holding without `avg_cost_basis` → `gain_loss=null`, `gain_loss_pct=null`.
- `test_yfinance_failure_for_one_ticker`: two holdings, yfinance fails for one → that holding has `current_price=null`, `price_fetch_error` set; other holding is unaffected; `total_value` excludes the failed ticker.
- `test_all_tickers_fail_raises_tool_error`: all yfinance calls fail → `ToolError` raised.
- `test_concurrency_bounded_to_4`: with 10 holdings/tickers, assert semaphore limits concurrent yfinance calls to ≤4 (mock semaphore or count concurrent calls).
- `test_top_gainers_and_losers`: 5 holdings with varying gain_loss_pct → top_gainers has top 3, top_losers has bottom 3.
- `test_ticker_validation_in_holding_create`: Pydantic rejects ticker `"aapl"`, `"APPLE INC"`, `""`, accepts `"AAPL"`, `"BRK.B"` (note: if dot not in regex, test that it is rejected).

### Integration tests (`test_portfolio_api.py`)
- `test_create_portfolio_201`: POST → 201, check returned UUID, name, empty holdings.
- `test_list_portfolios_empty`: GET before any create → 200, empty array.
- `test_list_portfolios_returns_only_own`: two users, each creates one portfolio → each only sees their own.
- `test_get_portfolio_404_other_user`: user A tries to GET user B's portfolio_id → 404.
- `test_delete_portfolio_cascades_holdings`: create portfolio + holding, DELETE portfolio → 204; subsequent GET holdings returns 404.
- `test_add_holding_invalid_ticker_422`: POST holding with ticker `"aapl"` → 422.
- `test_add_holding_invalid_shares_422`: POST holding with `shares=0` → 422.
- `test_delete_holding_204`: add then delete a holding → 204; subsequent GET holdings list is empty.
- `test_delete_holding_wrong_portfolio_404`: create two portfolios, try to delete a holding of portfolio A using portfolio B's ID → 404.

### Manual verification steps
1. Start backend: `uvicorn app.main:app --reload` in `backend/`.
2. Apply migration: `alembic upgrade head` — verify `portfolios` and `portfolio_holdings` tables exist with `\dt` in psql.
3. Create a portfolio via `curl -X POST /api/v1/portfolios -H "Authorization: Bearer <token>" -d '{"name":"Test"}'`.
4. Add AAPL (10 shares, cost $150) and MSFT (5 shares, no cost basis).
5. In the chat UI, type "How is my portfolio doing?" — verify the response contains specific dollar values for AAPL and MSFT.
6. Type "Which of my holdings is performing best?" — verify synthesizer names the ticker.
7. `docker compose restart api` — verify no import errors in startup logs.

## Observability

### Logs
| Event | Level | Fields |
|---|---|---|
| `tool_called` | DEBUG | `tool_name`, `user_id`, `holding_count` |
| `portfolio_price_fetch_failed` | WARNING | `ticker`, `error` |
| `portfolio_all_prices_failed` | ERROR | `user_id`, `ticker_count` |
| `tool_succeeded` | DEBUG | `tool_name`, `user_id`, `total_value`, `holding_count` |
| `portfolio_create` | DEBUG | `user_id`, `portfolio_id` |
| `portfolio_delete` | DEBUG | `user_id`, `portfolio_id` |
| `holding_create` | DEBUG | `user_id`, `portfolio_id`, `holding_id`, `ticker` |
| `holding_delete` | DEBUG | `user_id`, `portfolio_id`, `holding_id` |
| `portfolio_not_found` | WARNING | `user_id`, `portfolio_id` |

### Health indicators
- **Healthy**: `tool_succeeded` appears in logs with `total_value > 0` for users with holdings.
- **Degraded**: Frequent `portfolio_price_fetch_failed` warnings indicate yfinance upstream issues; individual holdings still returned with `price_fetch_error` set.
- **Unhealthy**: `portfolio_all_prices_failed` — tool raising `ToolError` for all users; likely yfinance is rate-limited or down.

## Risks and open questions

### Risks
1. **yfinance instability**: yfinance is an unofficial API scraper and can break when Yahoo Finance changes its HTML/API. Mitigation: individual ticker failures are graceful; only total failure raises `ToolError`. The existing `company_comparator.py` accepts the same risk.
2. **Stale prices in synthesizer output**: yfinance `fast_info.last_price` is the last traded price, which may be hours old after market close. The synthesizer should state "as of last close" but does not have explicit timestamp data from yfinance. This is acceptable for MVP.
3. **Migration order**: `0008` must come after `0007`. The `down_revision = "0007"` chain must be verified before deploying.

### Open questions
1. Should the `portfolio_analysis` tool also accept an optional `portfolio_id` parameter to query a single portfolio? Decision deferred — out of scope for now (agent always aggregates all portfolios). Can be added later without breaking the current interface.
2. Should `updated_at` on `PortfolioHolding` and `Portfolio` be updated via a SQLAlchemy `onupdate` event or a database trigger? Current models in the codebase (e.g., `User`) do not use `onupdate` — `updated_at` is set only at insert. For MVP, same convention: set `updated_at` at insert only; add trigger in a future migration if needed.
3. Should duplicate holdings for the same ticker in the same portfolio be prevented by a unique constraint? Decision: no constraint — users may have bought in multiple lots. The tool sums shares at analysis time.
4. Should ticker symbols be normalized to uppercase at the API layer before DB insert, or is Pydantic validation (regex `^[A-Z0-9]{1,10}$`) sufficient? Current decision: validation only — reject lowercase at 422 rather than silently coerce, consistent with how the existing tools handle ticker validation (`company_comparator.py` line 124).
