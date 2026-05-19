Write exactly this content to docs/specs/analyst-profile.md, replacing everything in the file:

# Spec: Analyst Profile

## Goal
Expose GET and PUT endpoints so an authenticated user can read and write their analyst profile — a set of preferences that will later be injected into the AI agent's system prompt.

## Background
The `analyst_profiles` table was created in migration `0001_initial_schema.py` with a one-to-one relationship to `users` (unique FK, CASCADE DELETE). The existing columns are `preferred_name`, `firm`, `role`, `sectors_of_interest`, `preferred_output_length`, and `preferred_citation_style`. The `AnalystProfile` SQLAlchemy model lives inside `backend/app/models/user.py` alongside `User`.

No API endpoints or Pydantic schemas exist for profile yet. The new columns required by this spec (`tracked_tickers`, `investment_style`, `preferred_output_format`, `custom_context`) do not exist in the table and must be added via a new migration. The existing Phase 1 columns are retained; this spec does not expose them through the new endpoints.

---

## Scope

### In scope
- Migration `0003` that adds `tracked_tickers`, `investment_style`, `preferred_output_format`, and `custom_context` columns to `analyst_profiles`, plus the two new PostgreSQL enum types they require.
- Add four new mapped columns to the existing `AnalystProfile` class in `backend/app/models/user.py`.
- Pydantic schemas `ProfileRead` and `ProfileUpdate` in `schemas/profile.py`.
- `GET /api/v1/profile` — returns the calling user's profile; auto-creates one with null/empty defaults if it does not exist yet.
- `PUT /api/v1/profile` — upserts the calling user's profile; all fields are optional (omitted fields are not changed); returns the full updated profile.
- Both endpoints are protected by `clerk_auth`.
- Structlog log lines for profile created, updated, and fetched.
- Registration of the new router in `api/v1/router.py`.

### Out of scope
- Multiple profiles per user.
- Profile sharing or team/org-level profiles.
- Profile versioning or audit log.
- Syncing preferences to Clerk user metadata.
- Exposing or modifying the Phase 1 columns (`preferred_name`, `firm`, `role`, `preferred_output_length`, `preferred_citation_style`) through these endpoints.
- Using the profile to influence AI agent behaviour (Phase 5).
- Any frontend UI — this is API-only.

---

## User flow

### GET /api/v1/profile — profile exists
1. Client sends `GET /api/v1/profile` with `Authorization: Bearer <jwt>`.
2. `clerk_auth` validates the JWT and resolves the `User` row.
3. Handler queries `analyst_profiles` by `user_id`.
4. Row found → serialise and return `ProfileRead` with HTTP 200.

### GET /api/v1/profile — first access (no row yet)
1–2. Same as above.
3. No row found for this `user_id`.
4. Handler inserts a new row with all Phase 2 columns set to `NULL` / empty-array defaults.
5. Returns `ProfileRead` with HTTP 200. (`created_at` and `updated_at` reflect the insertion time.)

### PUT /api/v1/profile — update preferences
1. Client sends `PUT /api/v1/profile` with valid JSON body (any subset of the five fields).
2. `clerk_auth` validates JWT.
3. Handler validates the body against `ProfileUpdate`:
   - `sectors` — list of strings, each 1–100 chars, list max 50 items.
   - `tracked_tickers` — list of uppercase strings matching `[A-Z]{1,10}`, list max 100 items.
   - `investment_style` — one of `"growth"`, `"value"`, `"blend"` (or absent).
   - `preferred_output_format` — one of `"concise"`, `"detailed"`, `"bullet_points"` (or absent).
   - `custom_context` — string, max 500 chars (or absent).
4. Validation fails → 422 with FastAPI's standard detail body; nothing is written.
5. Validation passes → upsert row (INSERT … ON CONFLICT DO UPDATE for the fields that were sent; omitted fields are not touched). `updated_at` is always refreshed.
6. Return full `ProfileRead` with HTTP 200.

### Error states
| Condition | HTTP status | Detail |
|---|---|---|
| Missing or invalid JWT | 401 | `"Not authenticated"` (from `clerk_auth`) |
| Field fails validation | 422 | FastAPI validation error body |
| DB error | 500 | logged; generic `"Internal server error"` |

---

## Detailed requirements

1. `GET /api/v1/profile` returns HTTP 200 in all authenticated cases — never 404.
2. On first GET, a profile row is created with `sectors_of_interest = NULL`, `tracked_tickers = NULL`, `investment_style = NULL`, `preferred_output_format = NULL`, `custom_context = NULL`.
3. The `ProfileRead` response serialises `sectors_of_interest` as the JSON key `sectors`; an absent DB value is returned as `[]`.
4. `tracked_tickers` absent in DB is returned as `[]` in `ProfileRead`.
5. `investment_style` and `preferred_output_format` absent in DB are returned as `null` in `ProfileRead`.
6. `custom_context` absent in DB is returned as `null` in `ProfileRead`.
7. `PUT /api/v1/profile` accepts a JSON body where every field is optional; an absent field means "do not change that column".
8. `investment_style` must be one of `"growth"`, `"value"`, `"blend"` if provided; any other value returns 422.
9. `preferred_output_format` must be one of `"concise"`, `"detailed"`, `"bullet_points"` if provided; any other value returns 422.
10. `custom_context`, if provided, must be ≤ 500 characters after stripping leading/trailing whitespace; exceeding the limit returns 422.
11. Each string in `sectors`, if provided, must be 1–100 characters; the list must contain at most 50 items.
12. Each string in `tracked_tickers`, if provided, must match the regex `^[A-Z]{1,10}$`; the list must contain at most 100 items.
13. A user cannot read or write any profile other than their own. The `user_id` filter is always derived from `clerk_auth`, never from the request body or URL.
14. Both endpoints return 401 when the `Authorization` header is missing or the JWT is invalid.
15. `updated_at` is set to the current UTC timestamp on every successful PUT, even if no values changed.
16. A structlog event `profile_created` (info) is emitted when auto-creation fires on GET.
17. A structlog event `profile_updated` (info) is emitted on every successful PUT. Both events include `user_id` and `profile_id`.

---

## Data model changes

### New PostgreSQL enum types (added in migration `0003`)

```sql
CREATE TYPE investment_style_enum AS ENUM ('growth', 'value', 'blend');
CREATE TYPE output_format_enum    AS ENUM ('concise', 'detailed', 'bullet_points');
```

### New columns on `analyst_profiles` (added in migration `0003`)

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `tracked_tickers` | `TEXT[]` | YES | NULL | Array of ticker symbols |
| `investment_style` | `investment_style_enum` | YES | NULL | New PG enum |
| `preferred_output_format` | `output_format_enum` | YES | NULL | New PG enum |
| `custom_context` | `TEXT` | YES | NULL | Max 500 chars enforced at app layer |

**No existing columns are dropped or renamed.** `sectors_of_interest` (Phase 1) is the column the new API reads and writes for the `sectors` field; no rename migration is needed.

### Indexes
No new indexes. This is a single-row-per-user table; the existing unique index on `user_id` (`uq_analyst_profiles_user_id`) covers all query patterns.

### Migration order
`0003_analyst_profile_fields.py` depends on `0002_add_conversations_deleted_at.py` (`down_revision = "0002"`). The two enum types must be created before the `ALTER TABLE` statements that reference them.

---

## API contracts

### GET /api/v1/profile

**Auth:** Required (Clerk JWT via `Authorization: Bearer`)

**Request:** No body, no query params.

**Response 200 — ProfileRead**
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "user_id": "1a2b3c4d-...",
  "sectors": ["Technology", "Healthcare"],
  "tracked_tickers": ["AAPL", "MSFT"],
  "investment_style": "growth",
  "preferred_output_format": "detailed",
  "custom_context": "Focus on small-cap names.",
  "created_at": "2026-05-19T10:00:00Z",
  "updated_at": "2026-05-19T10:00:00Z"
}
```

- `sectors`, `tracked_tickers` are always arrays (never `null`) — empty arrays when not set.
- `investment_style`, `preferred_output_format`, `custom_context` are `null` when not set.

**Response 401** — missing or invalid JWT.

---

### PUT /api/v1/profile

**Auth:** Required (Clerk JWT via `Authorization: Bearer`)

**Request body — ProfileUpdate** (all fields optional)
```json
{
  "sectors": ["Energy"],
  "tracked_tickers": ["XOM"],
  "investment_style": "value",
  "preferred_output_format": "bullet_points",
  "custom_context": "Prefer dividend stocks."
}
```

**Validation rules applied to the request body:**
- `sectors`: each item 1–100 chars; list ≤ 50 items.
- `tracked_tickers`: each item matches `^[A-Z]{1,10}$`; list ≤ 100 items.
- `investment_style`: one of `"growth"`, `"value"`, `"blend"`.
- `preferred_output_format`: one of `"concise"`, `"detailed"`, `"bullet_points"`.
- `custom_context`: stripped length ≤ 500.

**Response 200 — ProfileRead** (same schema as GET, reflecting the post-update state)

**Response 401** — missing or invalid JWT.

**Response 422** — validation failure; FastAPI standard error body.

---

## Component and file structure

### Backend — new files
| File | Purpose |
|---|---|
| `backend/alembic/versions/0003_analyst_profile_fields.py` | Adds `investment_style_enum`, `output_format_enum` types and four new columns to `analyst_profiles`. |
| `backend/app/schemas/profile.py` | `ProfileRead` and `ProfileUpdate` Pydantic models. |
| `backend/app/api/v1/profile.py` | `GET /api/v1/profile` and `PUT /api/v1/profile` route handlers. |

### Backend — modified files
| File | Change |
|---|---|
| `backend/app/models/user.py` | Add four new mapped columns to the existing `AnalystProfile` class. |
| `backend/app/api/v1/router.py` | `include_router(profile.router, prefix="/profile", tags=["profile"])`. |

---

## External dependencies
None. This feature uses only existing dependencies (FastAPI, SQLAlchemy async, asyncpg, Pydantic v2, structlog).

---

## Testing plan

### Manual verification (integration tests deferred to Docker environment)
1. Start backend: `uvicorn app.main:app --reload`.
2. `GET /api/v1/profile` with a valid Clerk JWT and no existing profile → 200, all nulls/empty arrays.
3. `PUT /api/v1/profile` `{"investment_style":"blend","sectors":["Technology"]}` → 200, values reflected.
4. `GET /api/v1/profile` → 200, same values as step 3.
5. `PUT /api/v1/profile` `{"investment_style":"invalid"}` → 422.
6. `GET /api/v1/profile` with no token → 401.

---

## Observability

| Event | Level | Fields |
|---|---|---|
| `profile_fetched` | debug | `user_id`, `profile_id` |
| `profile_created` | info | `user_id`, `profile_id` |
| `profile_updated` | info | `user_id`, `profile_id` |

---

## Risks and open questions

1. **Enum synchronisation** — `investment_style_enum` and `output_format_enum` are PostgreSQL native enums. If the values need to change in future, an additional migration is required to `ALTER TYPE … ADD VALUE`. This is harder to reverse than a text column.
2. **PUT semantics** — Implemented as partial update (omitted = no-op). A client that wants to clear a value must send `"sectors": []` explicitly.
3. **Auto-create race** — Two concurrent first GETs for the same user could both attempt insert; mitigated by `ON CONFLICT (user_id) DO NOTHING`.
4. **`custom_context` length** — Enforced at the Pydantic layer only. A direct DB write bypasses the check. Acceptable for now.