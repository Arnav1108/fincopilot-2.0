# Backend perf findings: slow simple GETs (`/conversations`, `/conversations/{id}/messages`)

Read-only investigation. No files modified, no migrations run.

Scope confirmed as non-issues before digging in: LangSmith/OpenAI env setup in `main.py` lifespan
runs once at startup, not per request; JWT verification uses `python-jose[cryptography]` (pyca
backend), which is fast (~1ms); `ConversationRead`/`MessageRead` schemas don't nest relationships,
so there's no N+1 from Pydantic serialization; `RequestLoggingMiddleware` only does in-process
`structlog` calls, no I/O.

## Findings, prioritized by impact × fix effort

### 1. DB reached via Railway's public TCP proxy instead of private networking
**Impact: very high · Effort: trivial (env var only)**

`backend/app/config.py:6` — `DATABASE_URL` resolves to the public proxy
(`zephyr.proxy.rlwy.net`, per the prod config). If the API container and Postgres are both
Railway services in the same project, traffic to the public proxy leaves the private network and
routes through Railway's edge/ingress layer, adding tens to hundreds of ms **per round trip**,
versus roughly ~1ms over the private network (`*.railway.internal`). Every other finding below is
a *count* of round trips; this finding is the *cost per round trip*, so it multiplies all of them.

**Fix:** point `DATABASE_URL` at the internal hostname (e.g. `postgres.railway.internal:5432`) for
service-to-service traffic; keep the public proxy only for external/local access. Pure infra
change, no code touched.

### 2. `clerk_auth()` does 3 sequential DB round trips on *every* request, including pure GETs
**Impact: high · Effort: low**

`backend/app/api/auth.py:66-78`. On every authenticated request, regardless of HTTP method:
1. `SELECT` user by `clerk_user_id` (indexed, fine on its own)
2. Unconditional `UPDATE users SET updated_at ...` + `db.commit()` — a write + commit on a pure
   GET request
3. `db.refresh(user)` — an extra `SELECT` after commit, purely to re-read a value the code already
   knows locally (`updated_at` was just set)

That's 3 sequential round trips spent on auth alone, before the endpoint's own queries even start.
Combined with #1, this is likely the single biggest contributor to the reported latency.

**Fix:**
- Drop `db.refresh(user)` — nothing downstream needs a value that wasn't already known before the
  commit.
- Don't write `updated_at` on every request. Either drop the "touch" entirely, or throttle it
  (e.g. only update if the existing `updated_at` is >5 min stale), turning it into an occasional
  write instead of one on every single GET.

### 3. `GET /conversations/{id}/messages` issues 2 sequential queries beyond auth
**Impact: medium · Effort: low**

`backend/app/api/v1/conversations.py:106-121` — ownership check (`SELECT` on `conversations`)
followed by a separate `SELECT` on `messages`. Combined with #2's 3 round trips, that's 5
sequential round trips for this endpoint alone.

**Fix:** fold the ownership check into the messages query (e.g. `JOIN conversations` or a
correlated `EXISTS` in the `WHERE` clause) to cut one round trip, or run the two queries
concurrently instead of sequentially if a single-query rewrite isn't desired.

### 4. `pool_pre_ping=True` with untuned pool, no explicit pool sizing
**Impact: medium (mostly resolved by #1) · Effort: low**

`backend/app/database.py:6` — `create_async_engine(settings.DATABASE_URL, echo=False,
pool_pre_ping=True)` with no `pool_size`/`max_overflow` set (falls back to SQLAlchemy defaults:
`pool_size=5`, `max_overflow=10`). `pool_pre_ping` issues a lightweight liveness check on every
connection checkout — correct/necessary behavior for a proxied connection that Railway may drop
when idle, but it's one more round trip per request over the same slow path as #1. Once #1 is
fixed this cost becomes negligible; until then it adds to the per-request tax.

**Fix:** keep `pool_pre_ping` (needed for proxy reliability), no change required here once DB
connectivity moves to the private network. Optionally set explicit `pool_size`/`max_overflow` to
document intent, but this is not itself a latency driver at current traffic levels.

### 5. No composite index backing `ORDER BY created_at` on `messages`
**Impact: low today, grows with data · Effort: trivial**

`backend/app/models/conversation.py:46` (`Message.conversation_id`, single-column index only) vs.
the query at `backend/app/api/v1/conversations.py:118-120`
(`WHERE conversation_id = :id ORDER BY created_at ASC`). At current row-counts-per-conversation
this is very unlikely to be the cause of multi-second latency (Postgres will filter via the index
then sort a small in-memory set), but flagging for when conversations grow long.

**Fix:** composite index `(conversation_id, created_at)` if/when message volume per conversation
grows large enough for the in-memory sort to matter.

### 6. JWKS fetch creates a new `httpx.AsyncClient()` per call
**Impact: negligible · Effort: trivial**

`backend/app/api/auth.py:23` — a fresh client (no connection reuse/keep-alive) is created each
time the 5-minute JWKS cache expires. Since this only runs once per 5 minutes rather than per
request, it is not a contributor to the reported per-request latency. Noting for completeness
only; not worth prioritizing.

## Summary

The two dominant, compounding factors are **(1)** every DB round trip paying public-proxy latency
instead of private-network latency, and **(2)** the auth dependency spending 3 of those round
trips (including an unnecessary write + commit + refresh) on every single request before the
endpoint logic runs. Fixing #1 (env var change) and #2 (drop `refresh`, stop writing `updated_at`
on every GET) should be the first two changes attempted, in that order, since #1 reduces the cost
of every round trip and #2 reduces the count of round trips spent on something orthogonal to the
actual request.
