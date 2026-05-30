# Spec: Portfolio Management UI

## Goal
Give users a dedicated page to create portfolios, manage holdings (add/delete), and view their current positions — replacing the current curl-only workflow.

## Background
The backend has 7 fully-implemented CRUD endpoints at `/api/v1/portfolios` (created in migration 0008). There is no frontend surface for them. Users who want to track holdings for the portfolio-analysis agent tool must currently hit the API manually. This spec adds the minimal UI to close that gap.

The existing frontend patterns to build on:
- `apiFetch` helper in `frontend/lib/api.ts` — all API calls go through it with Clerk Bearer tokens
- `frontend/lib/types.ts` — all TS interfaces live here
- `DocumentPanel.tsx` — reference for collapsible side-panel style with loading/empty states
- `Sidebar.tsx` — reference for sidebar item styling; needs a "Portfolio" nav link added
- shadcn/ui components already installed: `dialog`, `button`, `input`, `select`, `separator`, `badge`, `textarea`
- shadcn/ui components **not yet installed** that this feature needs: `table`, `form`, `label`

Routing constraint: the current chat layout at `app/chat/layout.tsx` wraps `ConversationsProvider` + `Sidebar`. A portfolio page at `/portfolio` (outside `chat/`) also needs the Sidebar. Rather than duplicate the layout, this spec introduces an `(shell)` App Router route group that shares the layout across both route trees.

## Scope

### In scope
- `(shell)` route group layout that provides `Sidebar` + `ConversationsProvider` to both `/chat/*` and `/portfolio`
- `/portfolio` page: list all user portfolios, create/delete portfolios, add/delete holdings
- TypeScript interfaces `PortfolioRead`, `HoldingRead`, `HoldingCreate`, `PortfolioCreate` in `types.ts`
- API functions `listPortfolios`, `createPortfolio`, `deletePortfolio`, `addHolding`, `deleteHolding` in `api.ts`
- "Portfolio" nav item in the sidebar footer area (above Settings)
- Create Portfolio dialog (name input)
- Add Holding dialog (ticker, shares, optional avg cost basis)
- Delete portfolio confirmation dialog (named, warns about cascade)
- Delete holding with inline confirmation (no separate dialog)
- Loading states on all async ops (buttons disabled, spinner text)
- Inline 422 validation errors
- Ticker input auto-uppercased; frontend regex validation before submit

### Out of scope
- Live price display in the portfolio panel
- Charts or performance metrics
- Import from CSV / broker sync
- Multiple portfolios displayed side-by-side
- Editing holdings (change shares/cost basis after creation)
- Portfolio renaming
- Any backend changes — all 7 endpoints are already done

## User flow

### Happy path

1. User opens the app. Sidebar loads with conversation list and footer buttons.
2. User sees a new **Portfolio** button in the sidebar footer (briefcase icon, above Settings).
3. User clicks Portfolio → navigates to `/portfolio`.
4. Page loads: calls `GET /api/v1/portfolios/` with Clerk token. Shows spinner while loading.
5. If no portfolios exist: empty-state message "No portfolios yet" + "New Portfolio" button.
6. User clicks "New Portfolio" → `CreatePortfolioDialog` opens.
7. User types portfolio name (e.g., "Tech Picks") → clicks Create → button shows "Creating…" while request is in flight.
8. `POST /api/v1/portfolios/` succeeds → dialog closes → new portfolio card appears at top of list.
9. Each portfolio card shows: name, creation date, holding count, "Add Holding" button, delete (trash) icon.
10. User clicks "Add Holding" on a portfolio → `AddHoldingDialog` opens.
11. User types ticker "aapl" → field auto-uppercases to "AAPL" on every keystroke.
12. User enters 10 shares, 150 cost basis → clicks Add → button shows "Adding…".
13. `POST /api/v1/portfolios/{id}/holdings` succeeds → dialog closes → holding row appears in the portfolio's holdings table.
14. Holdings table columns: Ticker | Shares | Avg Cost Basis | (delete action).
15. User clicks the trash icon on a holding row → row enters "confirm" state: background turns red-tinted, buttons show "Delete" and "Cancel".
16. User confirms → `DELETE /api/v1/portfolios/{id}/holdings/{holding_id}` → row disappears.
17. User clicks the trash icon on a portfolio card → `DeletePortfolioDialog` opens: "Delete 'Tech Picks'? This will permanently remove the portfolio and all X holdings."
18. User clicks "Delete" → `DELETE /api/v1/portfolios/{id}` → portfolio card disappears.

### Edge cases and error states

**Empty portfolio** — holdings table shows "No holdings yet. Click Add Holding to get started."

**Create portfolio — name too long** — Input has `maxLength={200}`. If the API returns 422 (e.g., whitespace-only name after server-side trim), the dialog shows the server error string inline below the input.

**Add holding — invalid ticker** — client validates `^[A-Z0-9]{1,10}$` before submit. If pattern fails, inline error "Ticker must be 1–10 uppercase letters or digits" appears; form does not submit.

**Add holding — shares ≤ 0** — inline error "Shares must be greater than 0"; form does not submit.

**Add holding — cost basis ≤ 0** — inline error "Cost basis must be greater than 0 if provided"; form does not submit.

**Add holding — 422 from API** — the `detail` field from FastAPI's validation error is parsed and shown inline. Since the backend schema mirrors these exact rules, this should only happen for floating-point edge cases.

**Portfolio not found (404)** — unlikely in normal flow (user can only see their own portfolios). If it occurs, show an error toast "Portfolio not found" and refresh the portfolio list.

**Network error** — `ApiError` with non-422/404 status → show a toast "Something went wrong. Please try again."

**Loading states** — all action buttons (Create, Add, Delete confirm) are `disabled` while their request is in flight. Button text changes to "Creating…" / "Adding…" / "Deleting…".

**Concurrent deletes** — no special handling needed; the list is refetched after each mutation.

## Detailed requirements

### Routing / layout
1. An `(shell)` App Router route group must be created so that `/chat/*` and `/portfolio` share one sidebar layout without duplicating `ConversationsProvider` or `Sidebar`.
2. `app/(shell)/layout.tsx` wraps children with `ConversationsProvider`, `Sidebar`, and the outer `div.flex.h-screen` container — identical to the current `app/chat/layout.tsx`.
3. `app/chat/layout.tsx` must be removed and its contents moved to `app/(shell)/layout.tsx`.
4. `app/chat/page.tsx`, `app/chat/[id]/page.tsx` must be moved to `app/(shell)/chat/page.tsx`, `app/(shell)/chat/[id]/page.tsx` with no logic changes.
5. After the move, all existing chat routes (`/chat`, `/chat/[id]`) must resolve identically to before.
6. `app/(shell)/portfolio/page.tsx` is the new portfolio page.

### Sidebar
7. The sidebar footer must include a Portfolio nav link rendered above the Settings button, using the `Briefcase` icon from `lucide-react`.
8. The nav link is active (highlighted with `bg-accent text-foreground`) when `pathname` starts with `/portfolio`; inactive otherwise.
9. Clicking the Portfolio link calls `router.push("/portfolio")`.

### Types (`frontend/lib/types.ts`)
10. Add `HoldingRead` interface: `id: string`, `portfolio_id: string`, `ticker: string`, `shares: string`, `avg_cost_basis: string | null`, `created_at: string`, `updated_at: string`. (`shares` and `avg_cost_basis` are `string` because FastAPI serializes `Decimal` as a JSON string.)
11. Add `PortfolioRead` interface: `id: string`, `user_id: string`, `name: string`, `created_at: string`, `updated_at: string`, `holdings: HoldingRead[]`.
12. Add `HoldingCreate` interface: `ticker: string`, `shares: string`, `avg_cost_basis?: string | null`.

### API functions (`frontend/lib/api.ts`)
13. `listPortfolios(token)` → `GET /portfolios/` → `Promise<PortfolioRead[]>`
14. `createPortfolio(token, name: string)` → `POST /portfolios/` body `{ name }` → `Promise<PortfolioRead>`
15. `deletePortfolio(token, portfolioId: string)` → `DELETE /portfolios/{portfolioId}` → `Promise<void>`
16. `addHolding(token, portfolioId: string, body: HoldingCreate)` → `POST /portfolios/{portfolioId}/holdings` → `Promise<HoldingRead>`
17. `deleteHolding(token, portfolioId: string, holdingId: string)` → `DELETE /portfolios/{portfolioId}/holdings/{holdingId}` → `Promise<void>`

### Portfolio page
18. The page must be a `"use client"` component.
19. On mount, call `listPortfolios` and store results in local state. Show a spinner (`text-muted-foreground` "Loading portfolios…") while fetching.
20. If the fetch throws `ApiError`, show an error banner with the message and a "Retry" button that re-triggers the fetch.
21. Portfolios are displayed as cards in a single-column list, ordered by `created_at` descending (API already returns this order).
22. Each card header shows: portfolio name (semibold), creation date in `MMM D, YYYY` format, and holding count badge.
23. Holdings for each portfolio are shown inside the card as a table. All portfolios start expanded (no accordion collapsing needed).
24. A "New Portfolio" button is shown at the top of the page at all times (not just empty state).

### Create Portfolio dialog
25. Opens via a `Dialog` (shadcn). Trigger: "New Portfolio" button.
26. Contains one `Input` for portfolio name, `maxLength={200}`, autofocused when dialog opens.
27. Name is trimmed before submission; if empty after trim, show inline error "Name is required" without submitting.
28. Submit button reads "Create" normally, "Creating…" while in flight, and is `disabled` during flight.
29. On success: close dialog, prepend the returned `PortfolioRead` to the local portfolio list state (no full refetch needed).
30. On 422: parse `detail` from the error body (try `JSON.parse(body).detail` → if array take `[0].msg`, else use as string) and display inline below the input.
31. Dialog resets to empty state when closed.

### Add Holding dialog
32. Opens via "Add Holding" button on each portfolio card.
33. Contains three fields: Ticker (`Input`), Shares (`Input` type="number"), Avg Cost Basis (`Input` type="number", optional).
34. Ticker input converts every keystroke to uppercase via `onChange: (e) => setValue(e.target.value.toUpperCase())`.
35. Client-side validation runs on submit (not on blur):
    - Ticker: must match `^[A-Z0-9]{1,10}$`; error "Ticker must be 1–10 uppercase letters or digits."
    - Shares: must be a positive number (parseFloat > 0); error "Shares must be greater than 0."
    - Cost basis: if non-empty, must be a positive number; error "Cost basis must be greater than 0."
36. Submit button reads "Add Holding" normally, "Adding…" while in flight.
37. On success: close dialog, append the returned `HoldingRead` to the relevant portfolio's holdings in local state.
38. On 422: show parsed API error inline below the form (same parsing logic as req 30).
39. Dialog resets to empty state when closed.

### Delete holding (inline)
40. Each holding row has a trash icon button (`Trash2` from lucide-react) at the far right.
41. Clicking it sets that row into `pendingDelete` state: row background becomes `bg-destructive/10`, trash icon is replaced by "Delete" (destructive variant button) and "Cancel" button.
42. Clicking "Cancel" clears `pendingDelete` state.
43. Clicking "Delete" fires `deleteHolding`; "Delete" button shows "Deleting…" and is `disabled`.
44. On success: remove holding from local portfolio state.
45. On error: clear `pendingDelete` state and show error toast.

### Delete portfolio dialog
46. Each portfolio card has a trash icon button in its header.
47. Clicking opens a `DeletePortfolioDialog` (shadcn `Dialog`): "Delete '{name}'? This will permanently remove the portfolio and all {n} holding(s). This cannot be undone."
48. Dialog has two buttons: "Cancel" (outline) and "Delete Portfolio" (destructive). "Delete Portfolio" shows "Deleting…" and is `disabled` while in flight.
49. On success: remove portfolio from local state, close dialog.
50. On error: close dialog and show error toast.

### Shadcn components
51. `npx shadcn-ui@latest add table` must be run before implementation; `Table`, `TableHeader`, `TableRow`, `TableHead`, `TableBody`, `TableCell` are used in `HoldingsTable`.
52. `npx shadcn-ui@latest add form` must be run; `Form`, `FormField`, `FormItem`, `FormLabel`, `FormControl`, `FormMessage` are used in both dialogs.
53. `npx shadcn-ui@latest add label` must be run for standalone label use if needed.

### TypeScript
54. The portfolio page and all components must compile with zero TypeScript errors (`npx tsc --noEmit`).
55. No use of `any`; all API responses typed via the interfaces from `types.ts`.

### Security
56. All API calls pass the Clerk token from `useAuth().getToken()` — never cache the token across renders.
57. Ticker display is rendered as text content (not `dangerouslySetInnerHTML`) to prevent XSS.

## Data model changes
No backend data model changes. Tables `portfolios` and `portfolio_holdings` are created in migration `0008_portfolio_tables.py` (already exists). This spec is frontend-only.

## API contracts
All endpoints already exist. Listed here for frontend implementation reference.

### POST /api/v1/portfolios/
- Auth: Clerk Bearer token (required)
- Request: `{ "name": string }` — name 1–200 chars
- Response 201: `PortfolioResponse` (see schema below)
- Response 422: FastAPI validation error

### GET /api/v1/portfolios/
- Auth: required
- Response 200: `PortfolioResponse[]` ordered `created_at DESC`, each with `holdings[]`

### DELETE /api/v1/portfolios/{portfolio_id}
- Auth: required
- Response 204: no body
- Response 404: `{ "detail": "Portfolio not found" }`

### POST /api/v1/portfolios/{portfolio_id}/holdings
- Auth: required
- Request: `{ "ticker": string, "shares": string|number, "avg_cost_basis"?: string|number|null }`
- ticker must match `^[A-Z0-9]{1,10}$`; shares > 0; avg_cost_basis > 0 if present
- Response 201: `HoldingResponse`
- Response 422: validation error

### DELETE /api/v1/portfolios/{portfolio_id}/holdings/{holding_id}
- Auth: required
- Response 204: no body
- Response 404: `{ "detail": "Holding not found" }`

### Response schemas (TypeScript mirror)
```typescript
// HoldingRead
{ id, portfolio_id, ticker, shares: string, avg_cost_basis: string|null, created_at, updated_at }

// PortfolioRead
{ id, user_id, name, created_at, updated_at, holdings: HoldingRead[] }
```

## Component and file structure

### Files to create
- `frontend/app/(shell)/layout.tsx` — shared shell layout: `ConversationsProvider` + `Sidebar` + outer `div.flex.h-screen` (content moved from `app/chat/layout.tsx`)
- `frontend/app/(shell)/chat/page.tsx` — moved from `app/chat/page.tsx`, no logic changes
- `frontend/app/(shell)/chat/[id]/page.tsx` — moved from `app/chat/[id]/page.tsx`, no logic changes
- `frontend/app/(shell)/portfolio/page.tsx` — portfolio page component: fetches portfolios, renders list
- `frontend/components/portfolio/CreatePortfolioDialog.tsx` — dialog for creating a portfolio
- `frontend/components/portfolio/AddHoldingDialog.tsx` — dialog for adding a holding; receives `portfolioId` and `onAdded` callback
- `frontend/components/portfolio/DeletePortfolioDialog.tsx` — confirmation dialog for portfolio deletion
- `frontend/components/portfolio/HoldingsTable.tsx` — renders holdings rows with inline delete confirmation

### Files to delete
- `frontend/app/chat/layout.tsx` — replaced by `(shell)/layout.tsx`

### Files to modify
- `frontend/app/chat/page.tsx` → move to `(shell)/chat/page.tsx`
- `frontend/app/chat/[id]/page.tsx` → move to `(shell)/chat/[id]/page.tsx`
- `frontend/lib/types.ts` — add `HoldingRead`, `PortfolioRead`, `HoldingCreate`
- `frontend/lib/api.ts` — add 5 portfolio API functions; add `PortfolioRead`, `HoldingRead`, `HoldingCreate` to the import list
- `frontend/components/sidebar/Sidebar.tsx` — add Portfolio nav link (Briefcase icon) above Settings button; add `usePathname` for active state

## External dependencies
- **Clerk** (`@clerk/nextjs`): `useAuth().getToken()` for Bearer token. Already in use. If Clerk is unavailable, all API calls fail with 401.
- **shadcn/ui** `table`, `form`, `label`: need to be installed via CLI before implementation. No network dependency at runtime.
- **lucide-react**: `Briefcase`, `Trash2` icons — already a dependency.

## Testing plan

### Unit tests (not automated in this spec — manual verification)
Not applicable for UI components that require browser rendering.

### Integration tests
- `frontend/tests/portfolio-api.test.ts` (if a test harness exists): mock `apiFetch` and verify each portfolio API function passes correct method, path, and body.

### Manual verification steps
1. Start backend (`uvicorn app.main:app --reload`) and frontend (`npm run dev`).
2. Navigate to `http://localhost:3000/chat` — verify chat still loads with sidebar.
3. Verify URL `/chat/[existing-id]` still works.
4. Click "Portfolio" in sidebar — verify navigation to `/portfolio`.
5. Page shows "No portfolios yet" + "New Portfolio" button.
6. Click "New Portfolio" → type "Test Portfolio" → Create → card appears.
7. Click "Add Holding" → enter "aapl" (auto-uppercases to AAPL), 10 shares, 150 cost basis → Add → row appears.
8. Verify holding shows AAPL | 10 | 150.
9. Click trash on the holding row → confirm state appears → click Delete → row removed.
10. Enter invalid ticker "aapl!!" → client error shown, no API call fired (check network tab).
11. Enter shares = -5 → client error shown.
12. Click portfolio trash → confirmation dialog shows portfolio name and holding count.
13. Confirm delete → portfolio card disappears.
14. Run `npx tsc --noEmit` in `frontend/` → zero errors.
15. Run `npm run lint` → zero errors.
16. Open browser console — zero errors logged.

## Observability
- No new backend logging is required (backend already logs portfolio_create, holding_create, etc. at DEBUG).
- Frontend: no new logging. Errors from `ApiError` are shown to the user inline; they do not need to be separately logged to the console.
- A healthy portfolio page: list loads within 500 ms on local dev, all mutations reflect immediately in UI state.
- An unhealthy state: any `ApiError` with status ≥ 500 — user sees generic error banner with Retry.

## Risks and open questions

1. **Route group migration** — Moving `app/chat/*` into `app/(shell)/chat/*` changes the filesystem layout but not the URL paths (route groups are transparent to the URL). The risk is that some import alias or layout.tsx `metadata` export breaks. Mitigation: run `npm run build` after the move before writing any new code.

2. **Decimal serialization** — FastAPI serializes `Decimal` fields as JSON strings (e.g., `"10.00000"`) not numbers. `HoldingRead.shares` is typed `string` here. When displaying, use `parseFloat(shares).toString()` to strip trailing zeros. If the API is ever changed to emit numbers, this will need updating.

3. **shadcn form vs. bare inputs** — The `form` component adds react-hook-form as a peer dependency. If the team prefers uncontrolled forms, bare `Input` + manual state is acceptable; the validation logic in requirements 35–36 can be implemented either way. Decision deferred to implementer.

4. **Holdings count on card** — `PortfolioRead.holdings` is returned in `GET /portfolios/` (loaded via `selectinload`), so the count is available without an extra request. If holdings grow large (100+), this may cause slow list responses; that's a future backend concern, not in scope here.

5. **Assumption** — `app/chat/layout.tsx` currently has no `export const metadata` or other Next.js-specific exports; if it does, those must be preserved in `(shell)/layout.tsx`.
