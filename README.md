# FinCopilot 2.0

**AI-powered financial research assistant for analysts who need answers, not dashboards.**

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14-black?logo=next.js)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-FF6F00)
![License](https://img.shields.io/badge/License-MIT-blue)

---

## What is FinCopilot 2.0?

FinCopilot 2.0 is a full-stack AI research assistant purpose-built for financial analysts. Ask it about any public company — revenue trends, balance sheet health, recent earnings — and it reasons through the question using a multi-node LangGraph agent that selects tools, executes them in parallel, and streams a cited answer back token by token over SSE. Complex questions ("compare Apple and Microsoft free cash flow and find this week's analyst coverage") produce structured multi-step plans. Simple ones ("what is AAPL's current price?") get answered with a single tool call.

What makes it interesting technically is the RAG pipeline. When you upload a document — an earnings transcript, a 10-K, an internal model — it gets chunked at 800-token windows with 100-token overlap, embedded with `text-embedding-3-small`, and indexed in PostgreSQL with pgvector. At query time, retrieval is hybrid: pgvector cosine similarity and PostgreSQL full-text search (`ts_rank_cd`) are merged with a configurable alpha, then reranked by a local cross-encoder (`ms-marco-MiniLM-L-6-v2`). SEC EDGAR filings are fetched on demand, previewed with a human-in-the-loop confirmation step, then ingested into the same pipeline — no manual downloading required.

Across sessions, the system quietly extracts analyst preferences (tickers you care about, sectors you follow, research patterns) from each conversation using GPT-4o-mini and stores them as structured facts. The next time you open a chat, the synthesizer already knows your context. Conversations auto-title on the first message, maintain a rolling 300-word summary every 6 turns, and every agent run is traced end-to-end in LangSmith.

---

## Architecture

```mermaid
flowchart TD
    Browser["Browser (Next.js 14)"]
    Clerk["Clerk Auth"]
    API["FastAPI :8000"]
    Router["Router Node\ngpt-4o-mini\nclassify: simple|complex|ingest"]
    ToolSelector["Tool Selector Node\ngpt-4o-mini\npick 1 tool"]
    Planner["Planner Node\ngpt-4o-mini\nbuild N-step plan (max 6)"]
    Executor["Executor Node\nrun tools concurrently (semaphore=4)"]
    Synthesizer["Synthesizer Node\ngpt-4o — stream tokens + extract chart"]

    FinData["financial_data\nyfinance — price, income,\nbalance sheet, cash flow"]
    WebSearch["web_search\nSerper / Tavily / Brave"]
    DocRetrieval["document_retrieval\nhybrid pgvector + FTS\n→ cross-encoder rerank"]
    DocFinder["document_finder\nSEC EDGAR fetch + HIL confirm\n→ ingest pipeline"]
    Comparator["company_comparator\nmulti-ticker side-by-side"]
    Portfolio["portfolio_analysis\nportfolio holdings + live prices"]

    PG["PostgreSQL 15 + pgvector\nHNSW index on embeddings\ntsvector for FTS"]
    Redis["Redis 7\nCelery broker + result backend\nSSE pub/sub + HIL confirm tokens"]
    Celery["Celery Worker\ningest_document\nextract_memories"]
    LangSmith["LangSmith\nfull agent trace per run"]

    Browser -- "multipart/form-data + JWT" --> API
    API -- "verify JWT" --> Clerk
    API -- "StateGraph.ainvoke" --> Router
    Router -- "simple" --> ToolSelector
    Router -- "complex" --> Planner
    ToolSelector --> Executor
    Planner --> Executor
    Executor --> FinData
    Executor --> WebSearch
    Executor --> DocRetrieval
    Executor --> DocFinder
    Executor --> Comparator
    Executor --> Portfolio
    DocRetrieval --> PG
    DocFinder --> Celery
    Executor --> Synthesizer
    Synthesizer -- "SSE token stream" --> Browser
    Synthesizer -- "SSE chart_data event" --> Browser
    Celery -- "embed + store chunks" --> PG
    Celery -- "extract memories" --> PG
    API --> Redis
    API -- "run_id" --> LangSmith
```

---

## Tech Stack

| Category | Technology | Purpose |
|---|---|---|
| **Frontend** | Next.js 14 (App Router) | SSR, routing, RSC |
| **UI** | shadcn/ui + Radix UI + Tailwind CSS | Component primitives, design system |
| **Charts** | Recharts | Agent-generated chart rendering |
| **Auth (frontend)** | Clerk (`@clerk/nextjs`) | Edge middleware + server-side auth |
| **Backend** | FastAPI 0.115 | Async REST API, SSE streaming |
| **Agent** | LangGraph 1.2 | Stateful multi-node graph with conditional edges |
| **LLMs** | OpenAI GPT-4o / GPT-4o-mini | Node-level model routing by cost vs. capability |
| **Auth (backend)** | Clerk JWKS JWT validation | Per-request user identity |
| **Database** | PostgreSQL 15 + pgvector | Vector storage, FTS, relational data |
| **ORM / Migrations** | SQLAlchemy 2.0 async + Alembic | Async sessions, 11 versioned migrations |
| **RAG Embeddings** | OpenAI text-embedding-3-small | 1536-dim document chunk vectors |
| **Reranker** | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Local cross-encoder for precision reranking |
| **Task Queue** | Celery 5.4 | Document ingestion + memory extraction |
| **Message Broker** | Redis 7 | Celery broker, result backend, HIL confirm tokens |
| **Financial Data** | yfinance | Real-time prices, income statements, balance sheets |
| **Web Search** | Serper / Tavily / Brave Search | Current news, analyst commentary |
| **SEC Filings** | SEC EDGAR REST API | 10-K, 10-Q fetch and ingestion |
| **Observability** | structlog + LangSmith | Structured JSON logs, full agent traces |
| **Testing** | pytest-asyncio + Playwright | Backend integration tests, E2E frontend tests |
| **Containerization** | Docker Compose | Orchestrated multi-service local stack |

---

## Features

### AI Agent
- **Intelligent query routing** — classifies every query as `simple`, `complex`, or `ingest` before touching a tool; avoids over-planning on trivial lookups
- **Multi-step planner** — decomposes complex queries into up to 6 ordered tool calls expressed as a validated JSON plan
- **Concurrent tool execution** — retrieval steps run sequentially (RAG context is stable before synthesis), all other steps run concurrently with a semaphore of 4
- **Model tiering** — cheap GPT-4o-mini for routing/planning/tool selection/memory extraction; GPT-4o only for synthesis where response quality matters
- **Token streaming** — every synthesizer response streams token by token over SSE; the frontend re-renders incrementally
- **Human-in-the-loop SEC ingestion** — agent previews a filing and sends a confirmation token; user types `CONFIRM:yes:<token>` to proceed; Redis manages TTLs

### Document Intelligence
- **Multi-format ingestion** — PDF, DOCX (with headings, tables, text boxes), CSV, TXT, HTML; all parsed in Celery workers
- **Hybrid search** — combines pgvector cosine similarity with PostgreSQL `ts_rank_cd` FTS; score merged as `α·cosine + (1−α)·BM25_norm` where `α=0.7` by default (configurable)
- **Cross-encoder reranking** — `ms-marco-MiniLM-L-6-v2` reranks the top 3× candidate set before returning the final `top_k` chunks to the synthesizer
- **Multi-document synthesis** — planner issues one `document_retrieval` step per document when comparing filings; synthesizer cites each source with `[N]` inline references
- **Real-time ingestion progress** — SSE `ingest_progress` events show pending / ready / failed counts; agent graph runs only after all uploads reach `DocumentStatus.ready`
- **SEC EDGAR on demand** — fetches any 10-K or 10-Q by ticker + form type, streams to a temp file, creates a `Document` record, and queues the Celery ingestion task

### Portfolio Tracking
- Create multiple named portfolios; add holdings with ticker, shares, and optional cost basis
- `portfolio_analysis` tool fetches live prices via yfinance and returns current value, P&L, and weight per holding
- Agent answers portfolio questions in natural language: "which of my holdings is performing best?"

### Chart Generation
- After every tool-result synthesis, a secondary GPT-4o call extracts structured chart data from the tool output
- Supports `line` (time series), `bar` (categorical), and `pie` (part-of-whole) chart types
- Chart payload arrives as a separate `chart_data` SSE event; `ChartBlock` renders it with Recharts in the message thread

### Cross-Session Memory
- After every assistant response, `extract_memories` Celery task calls GPT-4o-mini to extract analyst facts: `ticker_interest`, `sector_interest`, `investment_style`, `research_pattern`
- Stored in `user_memories` table (capped at 20 per user via FIFO eviction)
- Injected into agent state at the start of every chat turn as structured `analyst_profile` context
- Manageable via the Memories UI page with individual deletion

### Conversation Management
- Auto-title on first user message (GPT-4o-mini summarises the query into a headline)
- Rolling 300-word conversation summary regenerated every 6 messages — keeps long sessions coherent without unlimited context
- Soft-delete with `deleted_at`; conversations scoped to user via Clerk JWT
- RAG provenance tracking on every assistant message: `rag_used`, `relevance_score`, `retrieved_chunk_ids`, `agent_trace` (LangSmith URL)

### Developer Experience
- **Structured logging** — `structlog` with JSON output; every request, node transition, tool call, and task event carries a correlation ID
- **LangSmith tracing** — full agent run captured under a `run_id`; trace URL stored on the `Message` row
- **Alembic migrations** — 11 versioned migrations including HNSW index creation, tsvector column + GIN index for FTS, portfolio tables, and user memories
- **pytest-asyncio integration tests** — isolated async DB sessions per test; covers agent streaming, retrieval, ingestion, portfolio CRUD, memories API, context-var propagation
- **Playwright E2E tests** — fixture-based authentication, chat flow coverage
- **Per-node model overrides** — every node model is a separate env var so you can swap GPT-4o for a cheaper model per node in production without touching code

---

## Project Structure

```
FinCopilot-2.0/
├── frontend/                   # Next.js 14 application
│   ├── app/
│   │   ├── (shell)/            # Authenticated route group (shared sidebar layout)
│   │   │   ├── chat/           # Chat pages (new + [id])
│   │   │   ├── portfolio/      # Portfolio management page
│   │   │   └── memories/       # Cross-session memory viewer
│   │   ├── sign-in/            # Clerk catch-all route
│   │   └── layout.tsx          # Root layout with ClerkProvider
│   ├── components/
│   │   ├── chat/               # AgentStatus, ChartBlock, MessageBubble, InputBar, SourceList
│   │   ├── portfolio/          # CreatePortfolioDialog, HoldingsTable, AddHoldingDialog
│   │   ├── settings/           # SettingsModal, TagInput (analyst profile)
│   │   ├── sidebar/            # Sidebar, ConversationGroup, ConversationItem
│   │   └── ui/                 # shadcn/ui primitives
│   ├── hooks/                  # useStream, useMessages, useConversations
│   ├── lib/                    # api.ts (typed fetch client), types.ts
│   └── e2e/                    # Playwright E2E tests
│
├── backend/                    # FastAPI application
│   ├── app/
│   │   ├── agent/              # LangGraph graph
│   │   │   ├── graph.py        # StateGraph definition + conditional edges
│   │   │   ├── state.py        # AgentState TypedDict + MemoryManager
│   │   │   ├── router.py       # Query classifier (simple|complex|ingest)
│   │   │   ├── planner.py      # Multi-step plan generator
│   │   │   ├── tool_selector.py# Single-tool selector for simple queries
│   │   │   ├── executor.py     # Parallel tool runner + chunk normalizer
│   │   │   ├── synthesizer.py  # Streaming synthesis + chart extraction
│   │   │   └── stream_context.py # ContextVar-based SSE event queue
│   │   ├── tools/              # Tool implementations
│   │   │   ├── financial_data.py    # yfinance wrapper (price, IS, BS, CF)
│   │   │   ├── web_search.py        # Serper/Tavily/Brave Search
│   │   │   ├── document_retrieval.py# pgvector hybrid search
│   │   │   ├── document_finder.py   # SEC EDGAR fetch + HIL confirmation
│   │   │   ├── company_comparator.py# Multi-ticker metric comparison
│   │   │   └── portfolio_analysis.py# Portfolio holdings + live valuations
│   │   ├── services/
│   │   │   ├── retrieval.py    # Hybrid search + cross-encoder reranker
│   │   │   ├── sec_edgar.py    # EDGAR API client (CIK lookup, submissions)
│   │   │   ├── redis_pubsub.py # Redis pub/sub for document status SSE
│   │   │   └── title_generator.py  # Auto-title via GPT-4o-mini
│   │   ├── tasks/
│   │   │   ├── ingestion.py    # Celery: parse→chunk→embed→store
│   │   │   └── memory_extraction.py# Celery: extract analyst facts from conversation
│   │   ├── models/             # SQLAlchemy ORM models
│   │   │   ├── conversation.py # Conversation, Message (with RAG provenance)
│   │   │   ├── document.py     # Document, DocumentChunk (pgvector column)
│   │   │   ├── portfolio.py    # Portfolio, PortfolioHolding
│   │   │   ├── memory.py       # UserMemory (cross-session facts)
│   │   │   └── user.py         # User (Clerk-linked)
│   │   ├── api/v1/             # Route handlers
│   │   │   ├── chat.py         # POST /{id}/stream — the main SSE endpoint
│   │   │   ├── conversations.py# CRUD for conversations + messages
│   │   │   ├── documents.py    # Upload + status endpoints
│   │   │   ├── portfolios.py   # Portfolio + holdings CRUD
│   │   │   ├── memories.py     # Memory list + delete
│   │   │   ├── profile.py      # Analyst profile (name, focus, style)
│   │   │   └── webhooks.py     # Clerk webhook handler (user.created)
│   │   ├── config.py           # Pydantic-settings singleton
│   │   ├── database.py         # Async SQLAlchemy engine + session factory
│   │   └── main.py             # App factory, middleware, startup validation
│   ├── alembic/versions/       # 11 database migrations
│   └── tests/                  # pytest-asyncio integration tests
│
├── docs/specs/                 # Feature specifications (22 spec files)
├── docker-compose.yml          # api, celery-worker, postgres, redis
└── README.md
```

---

## Setup & Installation

### Prerequisites

| Tool | Version |
|---|---|
| Docker + Docker Compose | latest |
| Node.js | 20.x |
| Python | 3.11 |
| Git | any |

### 1. Clone the repository

```bash
git clone https://github.com/Arnav1108/FinCopilot-2.0.git
cd FinCopilot-2.0
```

### 2. Configure backend environment

```bash
cp backend/.env.example backend/.env
```

Open `backend/.env` and fill in:

```env
# Required
OPENAI_API_KEY=sk-...
CLERK_SECRET_KEY=sk_live_...
CLERK_JWKS_URL=https://<your-clerk-domain>/.well-known/jwks.json
CLERK_WEBHOOK_SECRET=whsec_...

# Search (at least one required for web_search tool)
SERPER_API_KEY=...
TAVILY_API_KEY=...
BRAVE_SEARCH_API_KEY=...

# SEC EDGAR (required for document_finder tool)
# Must be a valid email — EDGAR uses it to identify your client
SEC_EDGAR_CONTACT_EMAIL=you@example.com

# Observability (optional but recommended)
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=fincopilot-dev

# Database / Redis (defaults work with docker-compose)
DATABASE_URL=postgresql+asyncpg://fincopilot:fincopilot@localhost:5432/fincopilot
REDIS_URL=redis://localhost:6379/0
```

### 3. Configure frontend environment

```bash
cp frontend/.env.local.example frontend/.env.local
```

```env
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_...
CLERK_SECRET_KEY=sk_live_...
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 4. Start infrastructure with Docker Compose

```bash
docker-compose up postgres redis --detach
```

This starts PostgreSQL 15 with pgvector and Redis 7. Both have health checks — wait until they report healthy before proceeding.

### 5. Run database migrations

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate
source venv/bin/activate
pip install -r requirements.txt

alembic upgrade head
```

Alembic applies all 11 migrations including pgvector extension, HNSW index, tsvector + GIN index for FTS, portfolio tables, and user memories.

### 6. Start the backend

```bash
# API server
uvicorn app.main:app --reload --port 8000

# Celery worker (separate terminal, same venv)
celery -A app.celery_app worker --loglevel=info
```

Or run the full stack in Docker:

```bash
docker-compose up --build
```

### 7. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Sign up with Clerk and you're in.

### API keys — where to get them

| Key | Source |
|---|---|
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) — needs GPT-4o access |
| `CLERK_*` | [dashboard.clerk.com](https://dashboard.clerk.com) — create a new application |
| `SERPER_API_KEY` | [serper.dev](https://serper.dev) — 2,500 free queries/month |
| `LANGSMITH_API_KEY` | [smith.langchain.com](https://smith.langchain.com) — free tier available |
| `SEC_EDGAR_CONTACT_EMAIL` | Any valid email — EDGAR uses it as a courtesy user-agent identifier |

---

## How It Works

### Agent Architecture

Every chat message enters a LangGraph `StateGraph` as an `AgentState` — a plain JSON-serializable TypedDict that carries the query, conversation memory, analyst profile, tool results, RAG chunks, and chart data through every node.

**Router** classifies the query into `simple` (one tool or general knowledge), `complex` (multi-tool plan required), or `ingest` (user is uploading a local file). Classification uses a one-shot GPT-4o-mini call with a carefully engineered prompt and returns in a single word. Documents already ingested are flagged with `[has_documents: true]` so the router never misclassifies document questions as ingestion requests.

**Simple path** → **Tool Selector** picks the single best tool from the registry and constructs its input. **Complex path** → **Planner** generates a `{"steps": [...]}` JSON plan (validated against the tool registry; truncated to 6 steps if the model over-plans).

**Executor** separates `document_retrieval` steps from all other steps. Retrieval steps run sequentially first — so the RAG context is stable and chunk count is authoritative before any concurrent I/O starts. Non-retrieval steps run concurrently under an `asyncio.Semaphore(4)`. When `document_finder` successfully ingests a new filing, the executor immediately fires a follow-up retrieval so the new document is queryable in the same turn.

**Synthesizer** selects its system prompt based on what's available: RAG mode (chunks present), tool mode (financial data present), or LLM-only mode (no external data). It streams the GPT-4o response token by token, emitting `{"type": "token", "token": "..."}` events into an `asyncio.Queue` that the SSE layer drains. After the text response, a secondary LLM call extracts chart data from any tool results and emits it as a separate `chart_data` event.

### RAG Pipeline

Document ingestion is a Celery task (`ingest_document`) that runs in a separate worker process:

1. **Parse** — format-aware parsers for PDF (`pypdf`), DOCX (paragraphs + tables + text boxes with Markdown headings), CSV (batched rows), TXT, and HTML (BeautifulSoup)
2. **Chunk** — sliding window at 800 tokens (tiktoken `cl100k_base`) with 100-token overlap; page numbers tracked per chunk for attribution
3. **Embed** — `text-embedding-3-small` via OpenAI in batches of 200 with exponential-backoff retry on rate limit errors
4. **Store** — bulk insert of `DocumentChunk` rows; each chunk carries the pgvector embedding, a `tsvector` column (auto-populated via trigger), and metadata (page numbers, source filename, file type)

At query time, `RetrievalService.retrieve()` runs two candidate queries:

- **Vector query** — pgvector cosine distance via HNSW index, fetching `top_k × 3` candidates
- **FTS query** — `websearch_to_tsquery('english', query)` against the `content_tsv` GIN index, scored with `ts_rank_cd`

Results are merged with `hybrid_score = 0.7 × cosine + 0.3 × BM25_normalized`, sorted, then passed to the cross-encoder (`ms-marco-MiniLM-L-6-v2`) running in a thread pool via `asyncio.to_thread`. If either FTS or the reranker fails, the service falls back gracefully to pure vector order.

### SSE Streaming

The chat endpoint (`POST /api/v1/conversations/{id}/stream`) returns a `StreamingResponse` immediately and drives the agent in a background `asyncio.Task`. Agent nodes emit events into a `ContextVar`-bound `asyncio.Queue` via `emit_event()`. The streaming coroutine drains this queue and serializes each event as an SSE frame:

```
event: node_update
data: {"node": "router_node", "status": "running"}

event: tool_call
data: {"tool_name": "financial_data", "step_id": "step_0", "status": "running"}

event: token
data: {"token": "Apple's"}

event: chart_data
data: {"chart_type": "bar", "title": "Quarterly Revenue", ...}

event: done
data: {"message_id": "uuid", "conversation_id": "uuid"}
```

The frontend `useStream` hook consumes this with the native `EventSource` API, dispatching each event type to the appropriate UI state.

---

## Screenshots

> _Screenshots will be added after the first public demo build._

**Chat Interface**
> Full-width chat with collapsible document panel, streaming agent status indicators (node transitions, tool calls in progress), inline cited source list, and auto-rendered charts.

**Chart Generation**
> Recharts bar/line/pie rendered inline in the message thread based on agent-extracted financial data — no manual configuration required.

**Portfolio Page**
> Holdings table with current price, market value, and P&L; dialogs for creating portfolios and adding positions by ticker + share count.

**Memories Page**
> Paginated list of extracted analyst facts (ticker interests, sector focus, investment style, research patterns) with one-click deletion and confirmation dialog.

---

## Roadmap

- [ ] **Streaming evaluator** — add a retrieval quality evaluator node that retries retrieval with a reformulated query when `relevance_score < 0.4`; currently stubbed but not wired into the graph
- [ ] **Earnings comparison tool** — structured quarter-over-quarter diff from SEC filings, surfaced as a `ComparisonTable` component (spec in `docs/specs/earnings-comparison.md`)
- [ ] **Financial calculator tool** — DCF, ratio computation, and scenario analysis directly in the agent (removed from planner prompt pending proper schema design)
- [ ] **Multi-user portfolio sharing** — read-only portfolio share links for team workflows
- [ ] **OpenAI Structured Outputs** — migrate planner and synthesizer to strict JSON Schema mode to eliminate defensive parsing code
- [ ] **Streaming tool results** — emit partial tool data as it arrives rather than waiting for the full tool response before synthesis
- [ ] **Self-hosted embeddings** — optional SentenceTransformer embedding path so the ingestion pipeline works without an OpenAI key

---

## License

MIT License — see [LICENSE](LICENSE) for details.
