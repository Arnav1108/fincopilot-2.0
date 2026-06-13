# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project context
Financial research AI assistant. Full stack: Next.js 14 frontend, FastAPI backend, PostgreSQL with pgvector, Redis, Celery, LangGraph agent (not yet built). All specs live in `docs/specs/`. All prompts are broken into small verifiable steps.

## General rules
- Never skip verification steps
- Always confirm a step works before moving to the next
- When in doubt about scope, do less and ask
- Use `pyproject.toml` not `requirements.txt` for Python deps (note: `requirements.txt` and the Dockerfile still reference it — migrate when touching backend deps)
- Python version: 3.11. Node version: 20.

---

## Commands

### Frontend (`frontend/`)
```bash
npm run dev        # dev server on :3000
npm run build      # production build (also catches TS errors)
npm run lint       # ESLint via next lint
npx tsc --noEmit   # type-check without emitting
```

### Backend (`backend/`)
```bash
# activate venv first (Windows: venv\Scripts\activate)
source venv/bin/activate

uvicorn app.main:app --reload          # dev server on :8000
celery -A app.celery_app worker --loglevel=info   # Celery worker

alembic revision --autogenerate -m "describe change"   # generate migration
alembic upgrade head                                    # apply migrations
```

### Full stack (Docker)
```bash
docker-compose up --build     # start all services
docker-compose up postgres redis   # infra only (run api/frontend locally)
```

---

## Architecture

### Request flow
```
Browser → Next.js middleware (Clerk JWT check) → App Router page
                                                         ↓
                                               auth() server-side check
                                                         ↓
                                         fetch() → FastAPI /api/* → clerk_auth() dependency
                                                                            ↓
                                                                   request.state.user_id
```

### Frontend (`frontend/`)
- **Auth** is enforced at two layers: `middleware.ts` (edge, via `clerkMiddleware` + `createRouteMatcher`) and inside each Server Component via `await auth()`. Both must be kept in sync.
- `ClerkProvider` lives inside `<body>` in `app/layout.tsx` so it doesn't wrap `<html>` — this is intentional for Next.js 14 RSC compatibility.
- `/sign-in` and `/sign-up` use Clerk's catch-all route segments (`[[...sign-in]]`, `[[...sign-up]]`).
- `app/page.tsx` is a pure redirect: unauthenticated → `/sign-in`, authenticated → `/chat`.
- shadcn/ui is configured (slate base, CSS variables) via `components.json` and `tailwind.config.ts`. Add components with `npx shadcn-ui@latest add <component>`.

### Backend (`backend/`)
- All routes are mounted under the `/api` prefix in `main.py`. Never add routes outside this prefix.
- **Settings** (`app/config.py`) are a singleton pydantic-settings object imported as `from app.config import settings` everywhere. Add new env vars there first.
- **Database** (`app/database.py`): SQLAlchemy async engine using `asyncpg`. All models inherit from `Base` defined here. Use `get_db()` as a FastAPI dependency for sessions.
- **Auth** (`app/api/auth.py`): `clerk_auth()` is a FastAPI dependency that validates the `Authorization: Bearer <jwt>` header against Clerk's JWKS endpoint. The JWKS response is cached in a module-level dict with no TTL — restart the process to force a refresh.
- **Celery** (`app/celery_app.py`): broker and result backend both point to Redis. Tasks live in `app/tasks/` (not yet created). Register new task modules in the `include=[]` list.
- **Migrations**: Alembic is installed but not yet initialized. Run `alembic init alembic` inside `backend/` to set up, then point `sqlalchemy.url` at `settings.DATABASE_URL`.

### Infrastructure (`docker-compose.yml`)
- `api` and `celery-worker` build from the same `./backend` Dockerfile; only the `command` differs.
- `postgres` uses `pgvector/pgvector:pg15` — the pgvector extension must be explicitly enabled per database with `CREATE EXTENSION vector`.
- Both `postgres` and `redis` have health checks; `api` and `celery-worker` wait on them via `depends_on: condition: service_healthy`.
- Backend env is read from `backend/.env` (not committed). Copy `backend/.env.example` to start.

---
   