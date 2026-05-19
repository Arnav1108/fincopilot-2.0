# Spec: Auth and Logging

## Goal
Extend the existing Clerk JWT authentication to persist and return a full database `User` object on every request, add structured per-request logging, and add a Clerk webhook handler for user lifecycle sync — so every future endpoint has a working auth foundation and full request observability.

## Background
Today, `clerk_auth()` in `backend/app/api/auth.py` validates JWTs against Clerk's JWKS endpoint and attaches `clerk_user_id` to `request.state`. It never touches the database, so no endpoint has access to the user's internal UUID or any other user state. Every future endpoint that needs the user (which is all of them) would have to re-query the DB independently.

The `User` SQLAlchemy model exists in `backend/app/models/user.py` with columns: `id` (UUID PK), `clerk_user_id` (unique text), `email`, `display_name`, `is_active`, `created_at`, `updated_at`. The `get_db()` async session dependency exists in `backend/app/database.py`. No logging infrastructure exists — no request IDs, no timing, no structured output. No Clerk webhook handler exists, so user creation and deletion events from Clerk are never synced to the database (users only appear in the DB if they make an authenticated request first).

This spec completes the auth foundation every future endpoint depends on and adds the observability layer needed to debug and monitor the app.

## Scope

### In scope
- Extend `clerk_auth()` to accept a DB session, upsert a `User` row on every authenticated request, and return the full `User` ORM object (attached to `request.state.user`)
- Add `structlog` with per-request `request_id` (UUID4) propagated via `contextvars`
- Add `RequestLoggingMiddleware` that logs `method`, `path`, `status_code`, `duration_ms` on every request and sets the `X-Request-ID` response header
- Log `clerk_user_id` and `user_id` (DB UUID) on authenticated requests; log auth failures with reason
- Add `GET /api/v1/health/authed` — authenticated endpoint that proves the full auth stack works
- Add `POST /api/v1/webhooks/clerk` — Svix-verified webhook handler for `user.created` and `user.deleted`
- Add `LOG_LEVEL`, `CLERK_WEBHOOK_SECRET`, `APP_ENV` to `config.py` and `.env.example`
- Add `structlog` and `svix` to `requirements.txt`

### Out of scope
- RBAC or any authorization beyond the `is_active` boolean check
- Frontend logging of any kind
- Log aggregation, retention, or search UI (stdout only)
- Sentry or any external error tracker
- Rate limiting (separate spec)
- Any new endpoints beyond the authenticated health check and Clerk webhook
- Updating `updated_at` via a PostgreSQL trigger (application layer sets it explicitly per the database-schema spec)
- Handling Clerk webhook events other than `user.created` and `user.deleted`
- `user.updated` sync (email/display_name changes from Clerk)

## User flow

### Happy path: first authenticated request (new user)
1. Browser sends `GET /api/v1/health/authed` with `Authorization: Bearer <jwt>`
2. `RequestLoggingMiddleware` generates a UUID4 `request_id`, stores it in a `ContextVar`, records wall-clock start time
3. `clerk_auth()` fires:
   a. Extracts Bearer token from the Authorization header
   b. Fetches JWKS (from module-level cache) and validates the JWT signature; extracts `sub` as `clerk_user_id`
   c. Queries `users` by `clerk_user_id` — no row found
   d. Inserts a new `User` row: `is_active=True`, `created_at=now()`, `updated_at=now()`
   e. Attaches the `User` object to `request.state.user`
   f. Logs `clerk_user_id` and `user_id` at DEBUG level
4. Handler reads `request.state.user.id`, returns `{"status": "ok", "user_id": "<uuid>"}`
5. Middleware logs `method=GET path=/api/v1/health/authed status_code=200 duration_ms=<n> request_id=<uuid>`

### Happy path: returning authenticated request
Same as above, except step 3c finds the row and step 3d becomes: UPDATE `updated_at=now()` on the existing row. The same `user_id` UUID is returned.

### Happy path: Clerk webhook — user.created
1. Clerk (via Svix) sends `POST /api/v1/webhooks/clerk` with Svix signature headers and JSON body
2. Handler reads the raw request body bytes (required for Svix signature verification)
3. Verifies the Svix signature using `CLERK_WEBHOOK_SECRET` — valid
4. Parses event type: `user.created`
5. Extracts `data.id` as `clerk_user_id`; `data.email_addresses[0].email_address` as `email` if the list is non-empty; concatenated `data.first_name + " " + data.last_name` trimmed as `display_name` if both are non-empty
6. Runs `INSERT INTO users (clerk_user_id, email, display_name) VALUES (...) ON CONFLICT (clerk_user_id) DO NOTHING` — safe if auth-path upsert already created the row
7. Returns HTTP 200 `{"received": true}`
8. Logs `event_type=user.created clerk_user_id=<id>` at INFO

### Happy path: Clerk webhook — user.deleted
1. Clerk (via Svix) sends `POST /api/v1/webhooks/clerk`
2. Svix signature verified
3. Parses event type: `user.deleted`; extracts `data.id` as `clerk_user_id`
4. DELETEs the matching `User` row (cascade deletes all related rows via FK constraints)
5. If no row found, logs WARNING and still returns 200 (idempotent — Clerk may fire twice)
6. Returns HTTP 200 `{"received": true}`

### Edge case: unauthenticated request
1. Request has no `Authorization` header or an expired/invalid token
2. `clerk_auth()` raises `HTTPException(401)`
3. Middleware logs `status_code=401 auth_failed=true reason=<string>`
4. FastAPI returns 401 JSON to the client

### Edge case: inactive user
1. JWT is valid; user row exists in DB but `is_active=False`
2. `clerk_auth()` fetches the row, checks `is_active`, raises `HTTPException(403, "Account is disabled")`
3. Middleware logs `status_code=403`

### Edge case: Svix signature invalid
1. `POST /api/v1/webhooks/clerk` arrives with a wrong or tampered signature
2. `svix.Webhook.verify()` raises an exception
3. Handler returns HTTP 400 `{"detail": "Invalid webhook signature"}`
4. Logs ERROR with reason

### Edge case: CLERK_WEBHOOK_SECRET not configured
1. `CLERK_WEBHOOK_SECRET` is an empty string
2. Handler detects this before calling Svix and returns HTTP 400 immediately
3. Logs ERROR: `webhook_secret_not_configured=true`

### Edge case: unknown webhook event type
1. Clerk sends an event type the handler doesn't recognize (e.g., `user.updated`)
2. Handler returns HTTP 200 and logs INFO `unhandled_event_type=user.updated`
3. Must not return 4xx — Clerk retries on non-200, which would cause an infinite retry loop

### Edge case: JWKS fetch failure
1. Clerk JWKS endpoint is unreachable and the module-level cache is empty
2. `httpx` raises a network exception inside `_get_jwks()`
3. `clerk_auth()` catches it and raises `HTTPException(503, "Auth service unavailable")`
4. Middleware logs `status_code=503`

## Detailed requirements

### Auth — `clerk_auth()`

1. `clerk_auth()` must add a `db: AsyncSession = Depends(get_db)` parameter alongside its existing parameters.
2. After successful JWT validation, `clerk_auth()` must execute a SELECT on `users` filtered by `clerk_user_id = payload["sub"]`.
3. If no row is found, `clerk_auth()` must INSERT a new `User` row with `clerk_user_id` set; all other columns take their column defaults.
4. The upsert must use SELECT-then-INSERT (not `INSERT ... ON CONFLICT DO UPDATE`) to avoid overwriting `email` or `display_name` that the webhook handler may have set.
5. If a row is found, `clerk_auth()` must UPDATE `updated_at = now()` on that row using the ORM.
6. If the fetched or inserted `User` has `is_active=False`, `clerk_auth()` must raise `HTTPException(403, detail="Account is disabled")`.
7. `clerk_auth()` must attach the `User` object to `request.state.user`.
8. `clerk_auth()` must return the `User` object (return type changes from `str` to `User`).
9. `clerk_auth()` must log `clerk_user_id` and `user_id` (as string) at DEBUG level after the upsert, using the structlog logger with the current `request_id` bound via contextvars.
10. On any JWT validation failure (missing token, JWTError, missing `sub` claim), `clerk_auth()` must log `auth_failed=True` and a `reason` field at WARNING level before raising the HTTPException.
11. If `_get_jwks()` raises any exception (network error, non-2xx response), `clerk_auth()` must catch it and raise `HTTPException(503, detail="Auth service unavailable")`.
12. The existing module-level `_jwks_cache` dict and no-TTL behavior must be retained unchanged.

### Logging middleware

13. A `RequestLoggingMiddleware` class (inheriting from Starlette's `BaseHTTPMiddleware`) must be registered in `main.py` before the CORS middleware.
14. The middleware must generate one UUID4 `request_id` per request and store it in a `contextvars.ContextVar[str]` named `request_id_var` defined in `backend/app/logging.py`.
15. The middleware must record `time.perf_counter()` before calling `await call_next(request)` and compute `duration_ms = round((perf_counter() - start) * 1000, 2)` after the response is returned.
16. The middleware must emit exactly one log entry per request at INFO level with these fields: `request_id`, `method`, `path`, `status_code`, `duration_ms`.
17. The middleware must add the header `X-Request-ID: <request_id>` to every response.
18. The middleware must never log request or response bodies.
19. The middleware must never log the value of the `Authorization` header or any token.
20. The middleware overhead (excluding handler execution time) must be less than 5ms. No I/O may be performed inside the middleware.

### Structlog configuration

21. A `setup_logging()` function must be defined in `backend/app/logging.py` and called once from `main.py` inside the `lifespan` context manager before `yield`.
22. When `settings.APP_ENV == "production"`, structlog must render log entries as newline-delimited JSON to stdout using `structlog.processors.JSONRenderer`.
23. When `settings.APP_ENV != "production"` (default: `"development"`), structlog must render using `structlog.dev.ConsoleRenderer` with colors enabled.
24. The Python root logger level must be set to `settings.LOG_LEVEL` so that stdlib loggers (uvicorn, SQLAlchemy, httpx) respect the same configured level.
25. The structlog processor chain must include `structlog.contextvars.merge_contextvars` so that `request_id` bound to the contextvar appears automatically in every log entry within a request context.
26. Log entries must never include `email` or `display_name` values. These fields must not be passed to any log call anywhere in the codebase.

### Clerk webhook endpoint

27. `POST /api/v1/webhooks/clerk` must read the raw request body as `bytes` using `await request.body()` before any JSON parsing — required for Svix signature verification.
28. If `settings.CLERK_WEBHOOK_SECRET` is an empty string, the endpoint must return HTTP 400 `{"detail": "Invalid webhook signature"}` and log ERROR `webhook_secret_not_configured=True` without calling Svix.
29. The endpoint must call `svix.Webhook(settings.CLERK_WEBHOOK_SECRET).verify(body_bytes, headers_dict)` where `headers_dict` contains exactly the keys `svix-id`, `svix-timestamp`, and `svix-signature` extracted from the request headers.
30. If `svix.Webhook.verify()` raises any exception, the endpoint must return HTTP 400 `{"detail": "Invalid webhook signature"}` and log ERROR with the exception reason.
31. For `user.created` events: extract `data["id"]` as `clerk_user_id`; extract `data["email_addresses"][0]["email_address"]` as `email` only if `data["email_addresses"]` is non-empty; extract `display_name` from `data["first_name"]` and `data["last_name"]` only if both are non-empty strings. Execute `INSERT INTO users (clerk_user_id, email, display_name) ... ON CONFLICT (clerk_user_id) DO NOTHING`. Log INFO with `event_type` and `clerk_user_id`.
32. For `user.deleted` events: extract `data["id"]` as `clerk_user_id`. Execute DELETE on the `users` row. If affected row count is 0, log WARNING `user_not_found_for_delete=True clerk_user_id=<id>`. Return 200 regardless.
33. For all other event types: return HTTP 200 `{"received": true}` and log INFO `unhandled_event_type=<type>`. Do not return 4xx.
34. The webhook endpoint must not use `clerk_auth()` as a dependency — it is publicly callable but protected by Svix signature verification.
35. On success for any handled event, return HTTP 200 `{"received": true}`.

### Authenticated health endpoint

36. `GET /api/v1/health/authed` must declare `user: User = Depends(clerk_auth)` as its only dependency.
37. The endpoint must return HTTP 200 `{"status": "ok", "user_id": str(request.state.user.id)}`.
38. The endpoint must not issue any additional database queries; all DB work is done inside `clerk_auth()`.

### Config

39. `config.py` must add three new settings with these exact names, types, and defaults:
    - `LOG_LEVEL: str = "INFO"`
    - `CLERK_WEBHOOK_SECRET: str = ""`
    - `APP_ENV: str = "development"`
40. `backend/.env.example` must add:
    ```
    LOG_LEVEL=DEBUG
    CLERK_WEBHOOK_SECRET=
    APP_ENV=development
    ```

## Data model changes

No new tables or columns are required. The existing `users` table schema is sufficient:

| Column | Usage in this spec |
|--------|--------------------|
| `clerk_user_id TEXT NOT NULL UNIQUE` | Upsert key in `clerk_auth()` and webhook handler |
| `email TEXT` | Populated by `user.created` webhook; never logged |
| `display_name TEXT` | Populated by `user.created` webhook; never logged |
| `is_active BOOLEAN NOT NULL DEFAULT TRUE` | Checked in `clerk_auth()`; raises 403 if False |
| `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` | Set explicitly on every auth-path UPDATE (no trigger) |

**No Alembic migration is needed.**

The application layer sets `updated_at` explicitly on every UPDATE, per database-schema spec requirement 24 (no trigger).

## API contracts

### GET /api/v1/health/authed

**Auth:** Required (`clerk_auth()` dependency). JWT must be valid; user must be active.

**Request headers:**
```
Authorization: Bearer <jwt>
```

**Response 200:**
```json
{"status": "ok", "user_id": "550e8400-e29b-41d4-a716-446655440000"}
```

**Response 401** — missing or invalid token:
```json
{"detail": "Missing bearer token"}
{"detail": "Token validation failed: <jose reason>"}
{"detail": "Invalid token claims"}
```

**Response 403** — user is inactive:
```json
{"detail": "Account is disabled"}
```

**Response 503** — JWKS endpoint unreachable:
```json
{"detail": "Auth service unavailable"}
```

---

### POST /api/v1/webhooks/clerk

**Auth:** None. Publicly accessible; protected by Svix signature verification.

**Request headers:**
```
Content-Type: application/json
svix-id: <string>
svix-timestamp: <string>
svix-signature: <string>
```

**Request body — user.created:**
```json
{
  "type": "user.created",
  "data": {
    "id": "user_abc123",
    "email_addresses": [{"email_address": "user@example.com"}],
    "first_name": "Jane",
    "last_name": "Doe"
  }
}
```

**Request body — user.deleted:**
```json
{
  "type": "user.deleted",
  "data": {"id": "user_abc123"}
}
```

**Response 200:**
```json
{"received": true}
```

**Response 400** — signature invalid or secret not configured:
```json
{"detail": "Invalid webhook signature"}
```

HTTP status codes used: 200 (success or unhandled event type), 400 (signature failure only). Never 4xx for unknown event types.

---

## Component and file structure

### Backend — new files

| File | Purpose |
|------|---------|
| `backend/app/logging.py` | `setup_logging()`: configures structlog (JSON vs pretty based on APP_ENV), sets root logger level, defines `request_id_var: ContextVar[str]` |
| `backend/app/middleware/__init__.py` | Package marker |
| `backend/app/middleware/logging.py` | `RequestLoggingMiddleware`: generates request_id, times request, emits one log entry per request, sets X-Request-ID header |
| `backend/app/api/v1/__init__.py` | Package marker |
| `backend/app/api/v1/router.py` | v1 `APIRouter` that includes health and webhook sub-routers with their prefixes |
| `backend/app/api/v1/health.py` | `GET /health/authed` handler |
| `backend/app/api/v1/webhooks.py` | `POST /webhooks/clerk` handler with Svix verification and upsert/delete logic |

### Backend — modified files

| File | Change |
|------|--------|
| `backend/app/api/auth.py` | Add `db: AsyncSession = Depends(get_db)` parameter; add upsert logic; change return type to `User`; add structlog calls |
| `backend/app/config.py` | Add `LOG_LEVEL`, `CLERK_WEBHOOK_SECRET`, `APP_ENV` settings |
| `backend/app/main.py` | Call `setup_logging()` in lifespan; register `RequestLoggingMiddleware`; include v1 router at `/api/v1` prefix |
| `backend/.env.example` | Add `LOG_LEVEL`, `CLERK_WEBHOOK_SECRET`, `APP_ENV` entries |
| `backend/requirements.txt` | Add `structlog>=24.1.0` and `svix>=1.24.0,<2.0.0` |

### Tests — new files

| File | Purpose |
|------|---------|
| `backend/tests/test_auth.py` | Unit tests for `clerk_auth()` upsert paths, inactive user, JWKS failure |
| `backend/tests/test_webhooks.py` | Unit tests for Svix verification, user.created, user.deleted, unknown event |
| `backend/tests/test_logging_middleware.py` | Unit tests for request_id generation, log fields, X-Request-ID header |
| `backend/tests/test_health.py` | Integration test for `GET /api/v1/health/authed` end-to-end |

## External dependencies

| Dependency | Version | Purpose | If unavailable | Notes |
|-----------|---------|---------|----------------|-------|
| `structlog` | `>=24.1.0` | Structured logging, contextvars integration | App starts without logging | Add to `requirements.txt` |
| `svix` | `>=1.24.0,<2.0.0` | Svix webhook signature verification | Every webhook returns 400 | Clerk's delivery provider; pin minor version |
| Clerk JWKS endpoint | n/a | Provides public keys for JWT validation | Auth fails 503 until JWKS endpoint recovers | Cached in-process; cleared on restart |
| Clerk webhook delivery (Svix) | n/a | Delivers `user.created` / `user.deleted` events | Users not pre-created in DB; auth-path upsert still works as fallback | No known rate limit |

## Testing plan

### Unit tests

**`tests/test_auth.py`** — mock `_get_jwks()` to return a fixed payload and mock `AsyncSession`:
- Valid token, user not in DB → inserts row, returns `User`, `request.state.user` is set
- Valid token, user exists, `is_active=True` → updates `updated_at`, returns same `User`
- Valid token, user exists, `is_active=False` → raises `HTTPException(403)`
- Missing `Authorization` header → raises `HTTPException(401, "Missing bearer token")`
- Token present but `JWTError` during decode → raises `HTTPException(401)`, WARNING logged
- JWKS fetch raises network error → raises `HTTPException(503)`

**`tests/test_webhooks.py`** — mock `svix.Webhook.verify()` and `AsyncSession`:
- Valid signature, `user.created`, no existing row → row inserted, 200 returned
- Valid signature, `user.created`, row already exists → `ON CONFLICT DO NOTHING`, 200 returned, no duplicate
- Valid signature, `user.deleted`, row exists → row deleted, 200 returned
- Valid signature, `user.deleted`, no row found → WARNING logged, 200 returned
- Invalid Svix signature → 400 returned
- `CLERK_WEBHOOK_SECRET` is empty string → 400 returned, ERROR logged
- Unknown event type `user.updated` → 200 returned, INFO logged

**`tests/test_logging_middleware.py`** — use `httpx.AsyncClient` with the FastAPI app:
- Every response has `X-Request-ID` header containing a valid UUID4
- Two sequential requests produce different `request_id` values
- Log capture contains `request_id`, `method`, `path`, `status_code`, `duration_ms` for each request
- `duration_ms` is a positive float
- The string value of the `Authorization` header does not appear anywhere in captured log output

### Integration tests (require running Postgres + Clerk credentials)

**`tests/integration/test_health_authed.py`**:
- Unauthenticated `GET /api/v1/health/authed` → 401
- Valid Clerk JWT for a new user → 200, user row in `users` table, `response["user_id"]` == DB UUID
- Second request with same JWT → 200, same `user_id`, `updated_at` advanced relative to first request
- Token for a user with `is_active=False` in the DB → 403

### Manual verification checklist
1. `docker compose up postgres -d && uvicorn app.main:app --reload` (with `APP_ENV=development`)
2. Sign up via the frontend — within 5 seconds, `SELECT clerk_user_id FROM users;` returns 1 row (`user.created` webhook)
3. `GET /api/v1/health/authed` with a valid token → 200 with `user_id`
4. Uvicorn stdout shows human-readable log lines including `request_id`, `duration_ms`
5. Set `APP_ENV=production`, restart → stdout shows JSON objects
6. Send request with no Authorization header → 401; log shows `auth_failed=true reason=...`
7. Delete user from Clerk dashboard — within 5 seconds, `SELECT * FROM users WHERE clerk_user_id = '<id>';` returns 0 rows
8. Set `CLERK_WEBHOOK_SECRET` to wrong value → POST to `/api/v1/webhooks/clerk` → 400

## Observability

### Log event table

| Event | Level | Key fields (no PII) |
|-------|-------|---------------------|
| Request completed | INFO | `request_id`, `method`, `path`, `status_code`, `duration_ms` |
| Successful auth | DEBUG | `request_id`, `clerk_user_id`, `user_id` |
| Auth failure | WARNING | `request_id`, `auth_failed=True`, `reason` |
| New user inserted via auth upsert | INFO | `request_id`, `clerk_user_id`, `event=user_created_via_auth` |
| JWKS fetch failure | ERROR | `request_id`, `reason` |
| Webhook signature invalid | ERROR | `request_id`, `reason` |
| CLERK_WEBHOOK_SECRET not configured | ERROR | `request_id`, `webhook_secret_not_configured=True` |
| Webhook user.created processed | INFO | `request_id`, `event_type`, `clerk_user_id` |
| Webhook user.deleted processed | INFO | `request_id`, `event_type`, `clerk_user_id` |
| Webhook user.deleted — no row found | WARNING | `request_id`, `clerk_user_id`, `user_not_found_for_delete=True` |
| Unhandled webhook event type | INFO | `request_id`, `event_type` |

### Healthy vs unhealthy state
- **Healthy**: `GET /api/v1/health/authed` returns 200; every request log line includes `request_id` and `duration_ms`; new sign-ups produce a `users` row within 5 seconds; log output is valid JSON in production.
- **Unhealthy indicators**: 401s on valid tokens → JWKS cache stale (restart process); no `request_id` in logs → middleware not registered; users not appearing in DB after sign-up → `CLERK_WEBHOOK_SECRET` wrong or webhook URL not configured in Clerk dashboard; `duration_ms` missing → structlog `merge_contextvars` processor not in chain.

## Risks and open questions

1. **`clerk_auth()` return type is a breaking change**: Return type changes from `str` to `User` and a `db` dependency is added. Currently no endpoint uses `clerk_auth()` as a dependency, so there are no callers to update. Future endpoints must type-hint `user: User = Depends(clerk_auth)` and access the user object directly (not via `request.state.user_id` string).

2. **Race condition — auth upsert vs webhook**: A user may authenticate milliseconds before Clerk fires `user.created`. The auth-path SELECT-then-INSERT creates the row. The webhook's `INSERT ... ON CONFLICT (clerk_user_id) DO NOTHING` then safely no-ops. The reverse (webhook fires first, then auth) is also safe: SELECT finds the row, UPDATE runs. Both orderings are covered without explicit locking because the UNIQUE constraint on `clerk_user_id` prevents duplicates.

3. **`BaseHTTPMiddleware` and contextvars propagation**: In some Starlette versions, `BaseHTTPMiddleware` runs the downstream call in a new `asyncio.Task`, which does not inherit `ContextVar` values from the middleware's task. If this occurs, `request_id_var` will be empty in handler code. Test explicitly during implementation; if propagation fails, replace with a raw ASGI middleware that calls the app directly without spawning a new task.

4. **Uvicorn access logs mixing with structlog output**: Uvicorn emits access logs via stdlib `logging`. If the root logger's handler is not replaced with the structlog renderer, stdout will contain a mix of plain-text uvicorn lines and JSON structlog lines. The `setup_logging()` function must configure the root logger's handler to use the same structlog renderer so all output is uniform.

5. **`svix` package version pinning**: The `svix` Python SDK has had breaking interface changes between minor versions. Pin to `>=1.24.0,<2.0.0` in `requirements.txt` to avoid unexpected breakage on a `pip install --upgrade`.

6. **Email list may be empty in `user.created` payload**: Clerk supports phone-only sign-up. The webhook handler must guard against `data["email_addresses"]` being an empty list before accessing index 0 (requirement 31). Same for `first_name`/`last_name` being `None` or empty string.

7. **JWKS cache invalidation**: The no-TTL cache means a Clerk key rotation requires a process restart to take effect. This is a pre-existing limitation documented in CLAUDE.md; it is not addressed in this spec.
