# Spec: Frontend Memory UI

## Goal
Give users visibility into what the agent has learned about them and a one-click way to clear that data, via a Memories page accessible from the sidebar.

## Background
The cross-session memory system (`UserMemory` table, `/api/v1/memories` GET/DELETE) was built as part of the agent redesign but has no UI surface. Users have no way to audit or manage what the agent knows about them. The portfolio page (`frontend/app/(shell)/portfolio/page.tsx`) was the most recently built page and establishes the exact patterns this feature should follow: `useAuth`/`getToken`, `useCallback` load function, `useState` for data/loading/error, a card-based list, and a confirmation dialog for destructive actions.

## Scope

### In scope
- "Memories" nav link in the sidebar footer, between Portfolio and Settings
- `/memories` page listing all user memories from `GET /api/v1/memories`
- Each memory row shows: `fact_type` as a badge, `content` as text, `created_at` as relative age
- Loading state while fetching
- Empty state when no memories exist
- Error state with Retry button if the fetch fails
- "Clear All" button (only rendered when memories exist)
- Confirmation dialog before `DELETE /api/v1/memories` is called
- Optimistic UI update: list clears immediately on confirm, before API call resolves
- After clearing, list transitions to empty state

### Out of scope
- Editing individual memories
- Deleting individual memories
- Adding memories manually
- Filtering or categorizing memories by `fact_type`
- Backend changes of any kind
- Pagination (the list is expected to be small)

## User flow

**Happy path — viewing memories:**
1. User clicks "Memories" in the sidebar footer.
2. Browser navigates to `/memories`.
3. Page renders with a loading indicator while `GET /api/v1/memories` is in flight.
4. On success, memories are rendered as a list of cards/rows, one per memory.
5. Each row shows: a muted badge with `fact_type`, the `content` text, and a relative timestamp (e.g. "3d ago").

**Happy path — clearing memories:**
1. User clicks "Clear All" button (visible only when `memories.length > 0`).
2. A confirmation dialog opens: "Clear all memories? The agent will no longer remember anything it has learned about you. This cannot be undone."
3. User clicks "Clear All" in the dialog.
4. List clears immediately (optimistic update). Dialog closes.
5. `DELETE /api/v1/memories` fires in the background; 204 response is silently accepted.
6. Empty state is shown.

**Edge case — cancel confirmation:**
1. User clicks "Clear All" → dialog opens.
2. User clicks "Cancel" or presses Escape.
3. Dialog closes. List is unchanged.

**Edge case — fetch error:**
1. `GET /api/v1/memories` fails (network error or non-2xx).
2. Error message is shown with a "Retry" button.
3. User clicks Retry → load function runs again.

**Edge case — no memories yet:**
1. API returns `{ memories: [], count: 0 }`.
2. Page shows: "The agent hasn't learned anything about you yet." Empty-state copy.
3. "Clear All" button is not rendered.

**Edge case — delete fails:**
1. Optimistic clear has already emptied the UI.
2. `DELETE` returns an error.
3. Error toast or inline error is shown: "Failed to clear memories. Please try again."
4. List is reloaded from the API to restore accurate state.

## Detailed requirements

1. The sidebar footer renders a "Memories" button between the Portfolio button and the Settings button.
2. The Memories button uses the `Brain` icon from `lucide-react` (16px), matching the `Briefcase` and `Settings` icon sizes.
3. The Memories button applies the same active-route highlight as Portfolio: `bg-accent text-foreground` when `pathname?.startsWith("/memories")`, otherwise `text-muted-foreground hover:text-foreground hover:bg-accent`.
4. The `/memories` route renders inside the existing `(shell)` layout group, so the sidebar and `UserButton` are present without any layout changes.
5. The memories page title is "Memories" (H1, `text-xl font-semibold text-foreground`), matching the Portfolio page header style.
6. The page fetches memories on mount using the same `useCallback` + `useEffect` + cancellation pattern as `PortfolioPage`.
7. While fetching, the page renders `<p className="text-sm text-muted-foreground">Loading memories…</p>`.
8. On fetch error, the page renders the error message and a "Retry" `<Button variant="outline" size="sm">` that re-invokes the load function — identical to the Portfolio error UI.
9. When `memories.length === 0` and not loading and no error, the page renders: `<p className="text-sm text-muted-foreground text-center py-12">The agent hasn't learned anything about you yet.</p>`.
10. The "Clear All" button is a `<Button variant="destructive" size="sm">` rendered in the page header row, visible only when `memories.length > 0`.
11. Clicking "Clear All" opens a shadcn `AlertDialog` (not a custom modal).
12. The `AlertDialog` description text is exactly: "The agent will no longer remember anything it has learned about you. This cannot be undone."
13. The `AlertDialogAction` button text is "Clear All". The `AlertDialogCancel` button text is "Cancel".
14. On confirm, memories state is set to `[]` immediately (optimistic), then `DELETE /api/v1/memories` is called.
15. If the DELETE call throws, an inline error message is shown and `load()` is called to restore accurate state.
16. Each memory is rendered as a row inside a `rounded-lg border border-border bg-card` container, matching the portfolio card border style.
17. `fact_type` is rendered as `<span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground shrink-0">`, matching the holdings-count badge in `PortfolioCard`.
18. `content` is rendered as a `<p className="text-sm text-foreground">` with no truncation.
19. `created_at` is rendered as a relative age string using a local `formatRelativeAge` helper: seconds → "just now"; < 60 min → "{N}m ago"; < 24 h → "{N}h ago"; < 30 d → "{N}d ago"; older → localized date (e.g. "Jan 5, 2025").
20. `MemoryRead` and `MemoryListResponse` TypeScript interfaces are added to `frontend/lib/types.ts`.
21. `listMemories(token)` and `clearMemories(token)` functions are added to `frontend/lib/api.ts`, following the same `apiFetch` pattern.
22. `clearMemories` returns `Promise<void>` and maps to `apiFetch<void>("/memories", token, { method: "DELETE" })`.

## Data model changes
No database changes. No migrations needed.

## API contracts

### GET /api/v1/memories
- **Auth:** Required — `Authorization: Bearer <clerk_jwt>`
- **Request body:** None
- **Response 200:**
  ```json
  {
    "memories": [
      {
        "id": "uuid",
        "fact_type": "string",
        "content": "string",
        "conversation_id": "uuid | null",
        "created_at": "ISO 8601 datetime"
      }
    ],
    "count": 0
  }
  ```
- **Response 401:** Unauthorized (invalid/missing JWT)

### DELETE /api/v1/memories
- **Auth:** Required — `Authorization: Bearer <clerk_jwt>`
- **Request body:** None
- **Response 204:** No content — all caller memories deleted
- **Response 401:** Unauthorized

Both endpoints already exist and are unchanged by this spec.

## Component and file structure

### Frontend — new files
| File | Purpose |
|------|---------|
| `frontend/app/(shell)/memories/page.tsx` | Memories page: fetches and renders memory list; contains `ClearMemoriesDialog` inline (it's simple enough to not warrant a separate file) |

### Frontend — modified files
| File | Change |
|------|--------|
| `frontend/lib/types.ts` | Add `MemoryRead` and `MemoryListResponse` interfaces |
| `frontend/lib/api.ts` | Add `listMemories(token)` and `clearMemories(token)` |
| `frontend/components/sidebar/Sidebar.tsx` | Add Brain icon import and Memories nav button between Portfolio and Settings |

### Backend — no changes

### Tests — new files
| File | Purpose |
|------|---------|
| `frontend/app/(shell)/memories/page.test.tsx` | Unit tests for the memories page (optional — see testing plan) |

## External dependencies
No new dependencies. `AlertDialog` is already part of shadcn/ui. `Brain` is already in `lucide-react`.

Before writing code, verify `Brain` exists: `grep -r "Brain" frontend/node_modules/lucide-react/dist/lucide-react.js | head -1`. If absent, use `BookOpen` instead.

## Testing plan

### Manual verification steps (required before closing this spec)
1. Start dev server (`npm run dev` in `frontend/`).
2. Sign in and navigate to `/memories`.
3. Verify sidebar shows "Memories" link between Portfolio and Settings.
4. Verify "Memories" link is highlighted when on `/memories`.
5. With no memories in the database: verify empty-state text is shown and "Clear All" is absent.
6. Trigger memory creation (send a few chat messages so the agent learns preferences).
7. Reload `/memories` — memories appear with correct `fact_type` badges, content, and relative timestamps.
8. Click "Clear All" → dialog opens with correct copy.
9. Click "Cancel" → dialog closes, list unchanged.
10. Click "Clear All" again → confirm → list clears immediately → empty state shown.
11. Reload page — list is still empty (confirms DELETE actually fired).
12. Simulate fetch error (turn off backend) — error state with Retry button appears.
13. Click Retry after backend restarts — list reloads.

### Unit tests (optional but recommended)
- `formatRelativeAge` helper: test each time bucket (< 60s, < 1h, < 24h, < 30d, older).
- Page render: mock `listMemories` returning empty array → empty state copy present, "Clear All" absent.
- Page render: mock `listMemories` returning two items → two rows rendered.
- Clear flow: mock `clearMemories` resolving → list emptied, empty state shown.
- Clear flow: mock `clearMemories` rejecting → error shown, `listMemories` called again.

## Observability
- No new logging added on the frontend.
- Backend already logs `memories_cleared` at INFO level with `user_id` and `rows_deleted` when DELETE is called.
- Healthy state: `/memories` page loads in < 500 ms; GET `/api/v1/memories` returns 200 with a list.
- Unhealthy state: error state rendered; check backend logs for DB connectivity issues.

## Risks and open questions
- **`Brain` icon availability:** `lucide-react` ships with `Brain` since v0.263. The project likely has a recent version, but the fallback is `BookOpen`. Verify before writing the sidebar code.
- **Memory volume:** The list is rendered without pagination. If a user accumulates hundreds of memories (unlikely given the extraction cadence), the list may become unwieldy. This is explicitly out of scope but worth revisiting if memory extraction is made more aggressive.
- **Optimistic clear on DELETE failure:** Restoring state by calling `load()` means there's a brief moment where the list is empty before reloading. This is acceptable for the current scope; a rollback-first approach would add complexity.
- **`conversation_id` field:** `MemoryRead` includes `conversation_id` but it is not displayed in the UI per the spec. It is included in the TypeScript interface for completeness but not rendered.
