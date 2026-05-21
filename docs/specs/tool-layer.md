# Spec: Tool Layer (Phase 4 Foundation)

## Goal
Give the LangGraph agent a set of five typed, async-callable financial research tools it can look up by name from a central registry.

## Background
Phase 3 delivered the RAG ingestion pipeline (`backend/app/tasks/ingestion.py`) and retrieval service (`backend/app/services/retrieval.py`). The LangGraph agent (Phase 4) needs structured capabilities beyond document retrieval: live financial data, SEC filings, news, and financial ratio computation. Today none of these exist. The tool layer is the bridge between Phase 3 artifacts and the Phase 4 agent — it wraps existing services and adds four new external integrations.

Prior decisions that constrain this design:
- Python deps are managed via `requirements.txt` (not `pyproject.toml` — despite what CLAUDE.md says, the working backend uses requirements.txt).
- Settings are a singleton pydantic-settings object in `app/config.py`; all new env vars go there first.
- Async engine and `AsyncSessionFactory` are exported from `app/database.py`; tools use these directly.
- The existing `ingest_document` Celery task takes `(document_id: str, file_path: str)` — SEC tool must download to a temp file before dispatching it.
- Tools must be importable and callable without a running web server.

---

## Scope

### In scope
- `BaseTool` abstract base class with typed input/output generics
- `SECFilingFetchTool` — two-step fetch + ingest via EDGAR JSON API
- `FinancialDataTool` — income statement, balance sheet, cash flow, current price via yfinance
- `FinancialCalculatorTool` — pure-Python ratio computation (P/E, EV/EBITDA, ROE, D/E, revenue growth, margins)
- `NewsFetchTool` — article fetch via Tavily API
- `DocumentRetrievalTool` — RAG retrieval wrapping `RetrievalService` with metadata filters
- `TOOL_REGISTRY` dict in `backend/app/tools/__init__.py` mapping string names to tool instances
- Redis token-bucket rate limiter for SEC EDGAR (10 req/s) in `backend/app/tools/rate_limiter.py`
- Redis caching of SEC filing previews (1 hour TTL)
- New settings: `TAVILY_API_KEY`, `SEC_EDGAR_CONTACT_EMAIL`
- Unit tests for all five tools with mocked external calls
- Input/output Pydantic schemas for all five tools

### Out of scope
- LangGraph agent wiring (Phase 4)
- Frontend UI changes
- Document status SSE stream
- Peer Screener Tool (depends on Financial Data + Calculator — add after those two work)
- HTTP endpoints for tool invocation (tools are internal; no `/api/v1/tools/invoke` endpoint)
- Authentication on tool calls (tools are internal, called by the agent which is already authenticated)
- Streaming tool outputs
- Tool call history or audit logging

---

## User flow

Tools are invoked by the LangGraph agent, not directly by end users. The agent:

### Happy path — Document Retrieval
1. Agent calls `document_retrieval_tool(DocumentRetrievalInput(query="...", user_id=uuid, ticker="AAPL"))`.
2. Tool opens an async DB session, calls `RetrievalService.retrieve()` with a broader `top_k`, joins with `Document` to apply ticker/doc_type/fiscal_year filters, returns top 5 `ChunkResult` objects.
3. Agent receives `DocumentRetrievalOutput(chunks=[ChunkResult, ...])`.

### Happy path — SEC Filing (two-step)
1. Agent calls `sec_filing_tool(SECFilingInput(ticker="AAPL", filing_type="10-K"))`.
2. Tool checks Redis cache for `edgar_preview:AAPL:10-K`; cache miss.
3. Tool acquires a token from the Redis rate limiter (waits if at capacity).
4. Tool resolves `AAPL` → CIK via `https://data.sec.gov/submissions/CIK{cik}.json` (after ticker→CIK lookup).
5. Tool fetches the latest 10-K filing index, retrieves the primary document text URL.
6. Tool fetches the first 2000 characters of the filing text.
7. Tool generates a UUID confirmation token, stores `{ticker, filing_type, filing_url, company_name, filed_date}` in Redis under key `edgar_confirm:{token}` with 10-minute TTL.
8. Tool caches the preview under `edgar_preview:AAPL:10-K` with 1-hour TTL.
9. Returns `SECFilingPreviewOutput(preview="...", confirmation_token="...", company_name="...", filed_date="...", filing_url="...")`.
10. Agent presents preview to LLM, which decides to ingest.
11. Agent calls `sec_filing_tool(SECFilingInput(ticker="AAPL", filing_type="10-K", confirmation_token="<uuid>"))`.
12. Tool fetches confirmation data from Redis; validates it exists and is not expired.
13. Tool downloads the full filing to a temp file (e.g., `tempfile.NamedTemporaryFile`).
14. Tool looks up or creates a user row with `clerk_user_id="system"` (lazily on first call, cached in-process). Creates a `Document` DB record with `source_url`, `ticker`, `doc_type="10-K"`, `filing_date`, and `user_id=<system_user.id>` (see req 11).
15. Tool dispatches `ingest_document.delay(str(doc.id), temp_file_path)`.
16. Returns `SECFilingIngestOutput(document_id="...", status="pending")`.

### Happy path — Financial Data
1. Agent calls `financial_data_tool(FinancialDataInput(ticker="AAPL"))`.
2. Tool runs yfinance `Ticker("AAPL")` calls inside `asyncio.get_event_loop().run_in_executor(None, ...)`.
3. Returns `FinancialDataOutput` with income statement, balance sheet, cash flow (last 4 quarters TTM), and current price.

### Happy path — Financial Calculator
1. Agent (or tool chain) calls `financial_calculator_tool(FinancialCalculatorInput(...))` with data from `FinancialDataOutput`.
2. Tool performs pure Python arithmetic. Any field that cannot be computed (missing input) is returned as `None` with a corresponding `errors` list entry.
3. Returns `FinancialCalculatorOutput(pe_ratio=..., ev_ebitda=..., roe=..., ...)`.

### Happy path — News Fetch
1. Agent calls `news_fetch_tool(NewsFetchInput(ticker="AAPL", date_from=date(2024,1,1), max_results=10))`.
2. Tool calls Tavily search API with query `"AAPL stock news"` and date filter.
3. Returns `NewsFetchOutput(articles=[NewsArticle(...), ...])`.

### Edge cases and error states
- **Rate limit hit (EDGAR):** Tool blocks (async sleep) until a token is available, up to 5 seconds. If still unavailable after 5 seconds, raises `ToolRateLimitError`.
- **Confirmation token expired or not found:** `SECFilingFetchTool` raises `ToolValidationError("confirmation_token expired or invalid")`.
- **yfinance returns empty DataFrame:** `FinancialDataTool` returns `None` for the affected sub-field; does not raise.
- **Calculator receives `None` for a required input:** skips that ratio, adds entry to `errors` list, continues computing the rest.
- **Tavily API key missing:** `NewsFetchTool` raises `ToolConfigError` at call time (not import time).
- **EDGAR ticker not found:** `SECFilingFetchTool` raises `ToolNotFoundError("ticker {ticker} not found in EDGAR")`.
- **DB session failure in DocumentRetrievalTool:** exception propagates to the agent; tool does not swallow it.
- **No chunks match filters:** `DocumentRetrievalTool` returns `DocumentRetrievalOutput(chunks=[])` (empty list, not an error).

---

## Detailed requirements

### General
1. Every tool is a class that extends `BaseTool[InputT, OutputT]` and implements `async def __call__(self, input: InputT) -> OutputT`.
2. Every tool input and output is a Pydantic `BaseModel` with all fields typed and `None`-explicit where a field may be absent.
3. All tools are importable at module level without a running web server, Celery worker, or database connection — the connection is established lazily on first call.
4. `TOOL_REGISTRY: dict[str, BaseTool]` is exported from `backend/app/tools/__init__.py`. Keys are canonical string names: `"sec_filing"`, `"financial_data"`, `"financial_calculator"`, `"news_fetch"`, `"document_retrieval"`.
5. Tool errors are raised as subclasses of `ToolError(Exception)`: `ToolRateLimitError`, `ToolValidationError`, `ToolNotFoundError`, `ToolConfigError`, `ToolUpstreamError`.
6. Every tool logs at `DEBUG` level on entry and exit, and at `ERROR` level on failure, using `structlog`.

### SEC Filing Fetch Tool
7. First call (no `confirmation_token`): fetches filing preview, stores confirmation data in Redis, returns preview output. Second call (with `confirmation_token`): validates token, downloads file, creates Document record, dispatches ingestion task.
8. Ticker-to-CIK resolution: GET `https://www.sec.gov/files/company_tickers.json` (cached in module-level dict for the process lifetime — no Redis cache needed for CIK mapping). Raise `ToolNotFoundError` if ticker not found.
9. Rate limiter: Redis token bucket with key `edgar_ratelimit:{epoch_second}`. On each request, atomically INCR the key and SET expiry to 2 seconds (Lua script). If count > 10, async-sleep 0.1 s and retry, up to 5 s total before raising `ToolRateLimitError`.
10. EDGAR `User-Agent` header: `"FinCopilot Research Tool {settings.SEC_EDGAR_CONTACT_EMAIL}"`. This is required by SEC; tool raises `ToolConfigError` at call time if `SEC_EDGAR_CONTACT_EMAIL` is empty.
11. SEC filings are stored under a dedicated system user. On the first SEC tool call, the tool looks up a user row with `clerk_user_id="system"` in the `users` table; if it does not exist, it creates one. The result is cached in-process. All SEC `Document` records are owned by this system user. Retrieval always filters `WHERE user_id = :requesting_user_id`, so system documents are naturally invisible to regular users. No schema change is required.
12. Filing preview is cached in Redis as `edgar_preview:{ticker}:{filing_type}` (JSON-serialized `SECFilingPreviewOutput`) with TTL = 3600 seconds.
13. Confirmation token TTL: 600 seconds (10 minutes). Key: `edgar_confirm:{uuid4_hex}`.
14. HTTP client: `httpx.AsyncClient` with a 15-second timeout. Do not use `requests` (sync).

### Financial Data Tool
15. Wraps yfinance `Ticker` object. All yfinance calls run inside `asyncio.get_event_loop().run_in_executor(None, ...)` because yfinance is a sync library.
16. Returns TTM figures where applicable: income statement (revenue, gross_profit, operating_income, net_income, ebitda, eps_diluted), balance sheet (total_assets, total_debt, total_equity, cash_and_equivalents), cash flow (operating_cash_flow, capex, free_cash_flow, depreciation_amortization), and `current_price` (float).
17. If yfinance cannot fetch a specific sub-field (missing column, empty DataFrame), that field is `None` in the output — the tool does not raise.
18. Input validation: ticker must be 1–10 uppercase alphanumeric characters. Raise `ToolValidationError` otherwise.

### Financial Calculator Tool
19. Pure Python — no network calls, no DB calls. Must work with no external services running.
20. Computes: `pe_ratio`, `ev_ebitda`, `roe`, `debt_equity_ratio`, `revenue_growth_yoy`, `revenue_growth_3yr_cagr`, `gross_margin`, `operating_margin`, `net_margin`.
21. For each metric: if any required input field is `None`, that metric is `None` in the output and a human-readable string is added to `output.errors` (e.g., `"pe_ratio: eps_diluted is None"`). Computation of other metrics continues.
22. Formulas:
    - `pe_ratio = current_price / eps_diluted`
    - `ev_ebitda = (market_cap + total_debt - cash_and_equivalents) / ebitda`
    - `roe = net_income / total_equity`
    - `debt_equity_ratio = total_debt / total_equity`
    - `revenue_growth_yoy = (revenue_current - revenue_prior_year) / abs(revenue_prior_year)`
    - `revenue_growth_3yr_cagr = (revenue_current / revenue_3yr_ago) ** (1/3) - 1`
    - `gross_margin = gross_profit / revenue_current`
    - `operating_margin = operating_income / revenue_current`
    - `net_margin = net_income / revenue_current`
23. Division by zero: if denominator is zero, result is `None` and the formula name is added to `errors` with `"denominator is zero"`.

### News Fetch Tool
24. Uses Tavily Python SDK (`tavily-python`). API key sourced from `settings.TAVILY_API_KEY`. Raise `ToolConfigError` at call time if key is empty. The Tavily SDK is synchronous; all calls run inside `asyncio.get_event_loop().run_in_executor(None, ...)` — same pattern as yfinance.
25. Search query constructed as `"{ticker} stock financial news"`.
26. Returns up to `max_results` articles (default 10, max 25). Each article has: `title: str`, `url: str`, `summary: str | None`, `published_date: str | None`, `source: str | None`.
27. `date_from` and `date_to` are optional. If provided, passed as Tavily `search_depth` parameters. If Tavily does not support date filtering for the given plan, the tool logs a warning and returns results without date filtering (does not raise).
28. Tavily errors (connection failure, auth error) are wrapped in `ToolUpstreamError` and re-raised.

### Document Retrieval Tool
29. Wraps `RetrievalService.retrieve()`. `user_id` is a required input — the tool always scopes results to that user.
30. Optional filters: `ticker: str | None`, `doc_type: str | None`, `fiscal_year: int | None`. These are applied as SQL-level filters by JOINing `DocumentChunk` with `Document` inside the tool (not inside `RetrievalService`). The existing `RetrievalService.retrieve()` signature is not modified.
31. When filters are active: fetch `top_k * 4` candidates from `RetrievalService`, then apply metadata filters in Python, then return the top `top_k` by score. Log the pre/post filter counts.
32. Returns `DocumentRetrievalOutput(chunks: list[ChunkResult])`. Empty list (not an error) if no matches.
33. The tool manages its own async DB session via `AsyncSessionFactory` from `app.database`.

---

## Data model changes

No table schema changes. No migrations required.

SEC filings are owned by a system user (`clerk_user_id="system"`) that is created lazily on first SEC tool call. The `documents.user_id` column remains `NOT NULL`, which preserves the retrieval isolation guarantee.

### `app/config.py` additions
```python
TAVILY_API_KEY: str = ""
SEC_EDGAR_CONTACT_EMAIL: str = ""
```

---

## API contracts
No new HTTP endpoints. Tools are internal modules called by the agent.

---

## Component and file structure

### New files — Backend

| File | Purpose |
|------|---------|
| `backend/app/tools/__init__.py` | Exports `TOOL_REGISTRY: dict[str, BaseTool]` with all five tools keyed by canonical name |
| `backend/app/tools/base.py` | `BaseTool[I, O]` ABC; `ToolError` and its subclasses |
| `backend/app/tools/sec_filing.py` | `SECFilingFetchTool` with EDGAR fetch, Redis cache, rate limiter |
| `backend/app/tools/financial_data.py` | `FinancialDataTool` wrapping yfinance in thread pool |
| `backend/app/tools/financial_calculator.py` | `FinancialCalculatorTool` pure-Python ratio engine |
| `backend/app/tools/news_fetch.py` | `NewsFetchTool` wrapping Tavily SDK |
| `backend/app/tools/document_retrieval.py` | `DocumentRetrievalTool` wrapping RetrievalService with filters |
| `backend/app/tools/rate_limiter.py` | `RedisTokenBucket` async rate limiter (Lua script + redis.asyncio) |
| `backend/app/schemas/tools/__init__.py` | Re-exports all tool schemas |
| `backend/app/schemas/tools/sec_filing.py` | `SECFilingInput`, `SECFilingPreviewOutput`, `SECFilingIngestOutput` |
| `backend/app/schemas/tools/financial_data.py` | `FinancialDataInput`, `FinancialDataOutput`, `IncomeStatementData`, `BalanceSheetData`, `CashFlowData` |
| `backend/app/schemas/tools/financial_calculator.py` | `FinancialCalculatorInput`, `FinancialCalculatorOutput` |
| `backend/app/schemas/tools/news_fetch.py` | `NewsFetchInput`, `NewsFetchOutput`, `NewsArticle` |
| `backend/app/schemas/tools/document_retrieval.py` | `DocumentRetrievalInput`, `DocumentRetrievalOutput` |

### Modified files — Backend

| File | Change |
|------|--------|
| `backend/app/config.py` | Add `TAVILY_API_KEY` and `SEC_EDGAR_CONTACT_EMAIL` settings |
| `backend/requirements.txt` | Add `yfinance`, `tavily-python`, `httpx` dependencies |
| `backend/.env.example` | Add `TAVILY_API_KEY=` and `SEC_EDGAR_CONTACT_EMAIL=` |

### New files — Tests

| File | Purpose |
|------|---------|
| `backend/tests/tools/__init__.py` | Package marker |
| `backend/tests/tools/test_sec_filing.py` | Unit tests: preview fetch, cache hit, ingest dispatch, rate limit, token expiry, ticker not found |
| `backend/tests/tools/test_financial_data.py` | Unit tests: full data fetch, partial missing fields, invalid ticker |
| `backend/tests/tools/test_financial_calculator.py` | Unit tests: all ratios, None inputs, division by zero |
| `backend/tests/tools/test_news_fetch.py` | Unit tests: results returned, date filter passed, Tavily error wrapping, missing API key |
| `backend/tests/tools/test_document_retrieval.py` | Unit tests: no filters, ticker filter, empty result, user_id scoping |

---

## Input/output schemas (summary)

### SECFilingInput
```python
class SECFilingInput(BaseModel):
    ticker: str                        # e.g. "AAPL"
    filing_type: Literal["10-K", "10-Q"]
    confirmation_token: str | None = None
```

### SECFilingPreviewOutput
```python
class SECFilingPreviewOutput(BaseModel):
    preview: str                       # first 2000 chars of filing text
    confirmation_token: str            # UUID hex string
    company_name: str
    filed_date: str                    # ISO date string
    filing_url: str
    form_type: str
```

### SECFilingIngestOutput
```python
class SECFilingIngestOutput(BaseModel):
    document_id: str                   # UUID of created Document record
    status: Literal["pending"]
```

### FinancialDataInput
```python
class FinancialDataInput(BaseModel):
    ticker: str = Field(..., pattern=r"^[A-Z0-9]{1,10}$")
```

### FinancialDataOutput
```python
class IncomeStatementData(BaseModel):
    revenue_current: float | None
    revenue_prior_year: float | None
    revenue_3yr_ago: float | None
    gross_profit: float | None
    operating_income: float | None
    net_income: float | None
    ebitda: float | None
    eps_diluted: float | None

class BalanceSheetData(BaseModel):
    total_assets: float | None
    total_debt: float | None
    total_equity: float | None
    cash_and_equivalents: float | None

class CashFlowData(BaseModel):
    operating_cash_flow: float | None
    capex: float | None
    free_cash_flow: float | None
    depreciation_amortization: float | None

class FinancialDataOutput(BaseModel):
    ticker: str
    currency: str | None
    current_price: float | None
    market_cap: float | None
    income_statement: IncomeStatementData
    balance_sheet: BalanceSheetData
    cash_flow: CashFlowData
    as_of_date: str                    # ISO datetime string
```

### FinancialCalculatorInput
```python
class FinancialCalculatorInput(BaseModel):
    # Sourced from FinancialDataOutput fields
    current_price: float | None = None
    market_cap: float | None = None
    eps_diluted: float | None = None
    ebitda: float | None = None
    total_debt: float | None = None
    cash_and_equivalents: float | None = None
    total_equity: float | None = None
    net_income: float | None = None
    revenue_current: float | None = None
    revenue_prior_year: float | None = None
    revenue_3yr_ago: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
```

### FinancialCalculatorOutput
```python
class FinancialCalculatorOutput(BaseModel):
    pe_ratio: float | None = None
    ev_ebitda: float | None = None
    roe: float | None = None
    debt_equity_ratio: float | None = None
    revenue_growth_yoy: float | None = None
    revenue_growth_3yr_cagr: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    errors: list[str] = []
```

### NewsFetchInput
```python
class NewsFetchInput(BaseModel):
    ticker: str
    date_from: date | None = None
    date_to: date | None = None
    max_results: int = Field(10, ge=1, le=25)
```

### NewsFetchOutput
```python
class NewsArticle(BaseModel):
    title: str
    url: str
    summary: str | None = None
    published_date: str | None = None
    source: str | None = None

class NewsFetchOutput(BaseModel):
    ticker: str
    articles: list[NewsArticle]
```

### DocumentRetrievalInput
```python
class DocumentRetrievalInput(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    user_id: uuid.UUID
    top_k: int = Field(5, ge=1, le=20)
    ticker: str | None = None
    doc_type: str | None = None
    fiscal_year: int | None = None
```

### DocumentRetrievalOutput
```python
class DocumentRetrievalOutput(BaseModel):
    chunks: list[ChunkResult]          # reuses existing ChunkResult schema
```

---

## External dependencies

| Dependency | Purpose | Unavailable behavior | Rate limit |
|------------|---------|---------------------|------------|
| SEC EDGAR JSON API | Ticker→CIK lookup, filing index, filing text | Raises `ToolUpstreamError` | 10 req/s per IP — enforced by Redis token bucket |
| yfinance (PyPI) | Financial statements and price data | Returns `None` fields | None (best-effort scraping) |
| Tavily API | News article search | Raises `ToolUpstreamError` | Plan-dependent; not enforced by this tool |
| Redis | Token bucket, confirmation tokens, filing preview cache | Redis unavailable → `ToolUpstreamError` on SEC tool; other tools unaffected | N/A |
| OpenAI Embeddings API | Used by `RetrievalService` (already exists) | Propagates existing exception behavior | Already handled in RetrievalService |

---

## Testing plan

### Unit tests (mocked external calls)

**`test_sec_filing.py`**
- `test_preview_returns_correct_schema`: mock `httpx.AsyncClient.get` for EDGAR; assert output matches `SECFilingPreviewOutput`
- `test_cache_hit_skips_edgar_request`: prime Redis mock cache; assert EDGAR not called on second identical call
- `test_rate_limit_raises_after_timeout`: Redis mock returns count=11 for 50 iterations; assert `ToolRateLimitError` raised within 5s
- `test_ingest_dispatched_with_valid_token`: mock Redis confirmation lookup; assert `ingest_document.delay` called with correct args
- `test_expired_token_raises_validation_error`: Redis returns `None` for confirm key; assert `ToolValidationError`
- `test_unknown_ticker_raises_not_found`: EDGAR company_tickers.json mock excludes ticker; assert `ToolNotFoundError`
- `test_missing_contact_email_raises_config_error`: `settings.SEC_EDGAR_CONTACT_EMAIL = ""`; assert `ToolConfigError`

**`test_financial_data.py`**
- `test_full_output_schema`: mock yfinance `Ticker` with full DataFrames; assert all fields populated
- `test_partial_missing_fields`: mock yfinance returns empty DataFrame for cash flow; assert `cash_flow.*` fields are `None`, other fields populated
- `test_invalid_ticker_raises_validation_error`: ticker `"a b"` (contains space); assert `ToolValidationError` before any yfinance call
- `test_runs_in_executor`: verify yfinance calls do not execute on the event loop thread

**`test_financial_calculator.py`**
- `test_all_ratios_correct`: provide full inputs; verify each ratio against expected float (±0.001)
- `test_none_input_produces_none_output_and_error_entry`: set `eps_diluted=None`; assert `pe_ratio=None` and `"pe_ratio"` in `output.errors`
- `test_division_by_zero_produces_none`: `total_equity=0`; assert `roe=None` and error entry says "denominator is zero"
- `test_all_none_inputs`: all inputs None; assert all ratios None, `len(errors) == 9`
- `test_partial_inputs_compute_available_ratios`: provide only revenue fields; assert margin ratios computed, others None

**`test_news_fetch.py`**
- `test_returns_articles_matching_schema`: mock Tavily SDK response; assert `NewsFetchOutput` with correct article count
- `test_max_results_respected`: request 3; assert only 3 articles returned
- `test_tavily_error_wrapped`: Tavily raises `Exception`; assert `ToolUpstreamError` raised
- `test_missing_api_key_raises_config_error`: `settings.TAVILY_API_KEY = ""`; assert `ToolConfigError`

**`test_document_retrieval.py`**
- `test_no_filters_returns_top_k`: mock `RetrievalService.retrieve()` returning 5 chunks; assert all 5 returned
- `test_ticker_filter_removes_non_matching_chunks`: mock returns 10 chunks, 3 match ticker; assert 3 returned
- `test_empty_result_is_not_error`: mock returns empty list; assert `chunks=[]` in output, no exception
- `test_user_id_always_passed_to_retrieval_service`: assert `retrieve()` called with the exact `user_id` from input

### Integration tests
- `test_tool_registry_contains_all_five`: import `TOOL_REGISTRY`; assert all five keys present and values are `BaseTool` instances
- `test_calculator_with_financial_data_output`: call `FinancialDataTool` with a live ticker (skip if `YFINANCE_AVAILABLE=false`), pipe output to `FinancialCalculatorTool`, assert no validation errors

### Manual verification
1. From a Python REPL with venv active and `.env` loaded: `from app.tools import TOOL_REGISTRY; print(list(TOOL_REGISTRY.keys()))` → five keys printed.
2. Call `FinancialCalculatorTool` with hard-coded inputs for AAPL 2023; verify ratios are within expected ranges.
3. Call `SECFilingFetchTool("AAPL", "10-K")` step 1 → confirm preview returned; step 2 with token → confirm Celery task queued.
4. Call `DocumentRetrievalTool` with a query and a real user_id that has ingested docs; verify chunks returned match ticker filter.

---

## Observability

| Event | Level | Fields |
|-------|-------|--------|
| Tool called | DEBUG | `tool_name`, `input_summary` (no PII) |
| Tool succeeded | DEBUG | `tool_name`, `elapsed_ms`, `output_summary` |
| Tool failed | ERROR | `tool_name`, `error_type`, `error_message` |
| SEC rate limiter: token acquired | DEBUG | `epoch_second`, `count` |
| SEC rate limiter: waiting | WARNING | `epoch_second`, `wait_attempt` |
| SEC cache hit | DEBUG | `ticker`, `filing_type` |
| SEC cache miss | DEBUG | `ticker`, `filing_type` |
| Document Retrieval: pre/post filter | DEBUG | `pre_filter_count`, `post_filter_count`, `filters_applied` |

Healthy state: all five tools importable, `TOOL_REGISTRY` populated, calculator produces non-None ratios for a liquid US equity with available yfinance data.

Unhealthy state: `ToolConfigError` on startup (missing env vars), Redis unavailable causing SEC tool to fail every call, yfinance rate-limiting causing all financial data to return `None`.

---

## Risks and open questions

1. **yfinance reliability**: yfinance scrapes Yahoo Finance HTML and breaks periodically when Yahoo changes its page structure. If it fails in production, the Financial Data Tool will return mostly-None output. Mitigation: the output schema treats all fields as `Optional` so downstream code (agent, calculator) degrades gracefully.

2. **SEC EDGAR company_tickers.json size**: The file is ~7 MB and is cached in-process. On a fresh process start the first call will spend time fetching it. Mitigation: fetch eagerly at import time in a background task, or accept the first-call latency.

3. **System user bootstrap race**: If two concurrent requests trigger the lazy system-user creation simultaneously, both may attempt to INSERT the `clerk_user_id="system"` row and one will hit a unique constraint violation. Mitigation: wrap the lookup-or-create in a `SELECT ... FOR UPDATE` or use `INSERT ... ON CONFLICT DO NOTHING` to make it idempotent.

4. **Tavily date filtering**: Tavily's Python SDK date filtering behavior varies by plan tier. The spec assumes best-effort filtering; if date params are silently ignored, the tool still returns results (logged as a warning). This is deferred to implementation-time discovery.

5. **`ingest_document` task expects a local file path**: The SEC tool must download the full filing to a temp file before dispatching the task. For large 10-K filings (can be 50–200 MB of HTML), this adds latency and disk usage. Mitigation: download to `tempfile.NamedTemporaryFile(delete=False)`, dispatch task, task will clean up (it calls `os.remove` at the end). Streaming download with `httpx` to avoid loading entire file in memory.

6. **Thread safety of yfinance ticker cache**: yfinance uses internal module-level state. Running multiple concurrent `run_in_executor` calls for the same ticker may cause race conditions in yfinance internals. Mitigation: treat each `run_in_executor` call as isolated; acceptable risk for MVP.
