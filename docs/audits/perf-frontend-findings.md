# Frontend Perf Audit — Waterfalls & Bundle Size

Read-only investigation. Scope: `app/(shell)/chat` + `chat/[id]`, data-fetching hooks, `lib/api.ts`, root layout, `Sidebar`, `DocumentPanel`, `ChartBlock`/`MessageBubble`.

No files were changed as part of this audit.

---

## Prioritized findings (impact × effort)

| # | Finding | Impact | Effort | Priority |
|---|---------|--------|--------|----------|
| 1 | `/chat` root redirect is fully client-side and gated on a data fetch | High | Low–Med | **P0** |
| 2 | `DocumentPanel` fetch is sequential-after-messages, not parallel | Med | Low | **P1** |
| 3 | Per-hook `getToken()` calls create an extra promise-hop per fetch | Low | Low | P2 |
| 4 | No bundle analyzer wired up to verify tree-shaking assumptions | Low | Low | P2 |
| 5 | Recharts already code-split correctly | — | — | ✅ No action |
| 6 | Server/client boundaries already drawn correctly | — | — | ✅ No action |

---

## 1. `/chat` root page redirect is a client-side waterfall (P0)

**File:** `app/(shell)/chat/page.tsx`

This page is `"use client"` and does nothing but wait for data before redirecting:

```
mount → Clerk hydrates → getToken() → GET /api/v1/conversations/
      → isLoading=false → useEffect fires router.replace(`/chat/${id}`)
      → ConversationPage mounts → getToken() (cheap, cached) → GET /messages
```

Every user who lands on the bare `/chat` route (fresh sign-in, bookmark, hard refresh, clicking the logo) pays for a full client JS boot + Clerk hydration + one round-trip fetch **before the redirect to the real conversation even starts**, then a second round-trip for messages. `TranscriptSkeleton` is shown for the entire span, so it's not a blank-screen problem, but it is a real time-to-content problem — two sequential network requests plus client hydration stand between navigation and usable content, none of which is necessary.

By contrast, `app/page.tsx` (the `/` → `/chat` hop) already does this correctly as an async Server Component:

```tsx
export default async function Home() {
  const { userId } = await auth();
  if (!userId) redirect("/sign-in");
  redirect("/chat");
}
```

**Proposed fix:** Convert `app/(shell)/chat/page.tsx` into an async Server Component that resolves the Clerk session token server-side (`auth()` / `auth().getToken()`), fetches the conversation list directly from the backend, and calls `redirect(`/chat/${latest.id}`)` before any HTML reaches the client — collapsing the "fetch list → client redirect → fetch messages" chain into "fetch list server-side → redirect → fetch messages." The empty-state UI (no conversations yet) would need to move into this server component too, or stay as a client fallback rendered only when the server-side list is empty.

If a server-side fetch to the FastAPI backend isn't easily reachable from this route (e.g., `NEXT_PUBLIC_API_URL` is browser-only), at minimum note that as a blocker — a same-origin server-to-server URL for the backend would need to exist first.

---

## 2. `DocumentPanel` fetch waits on messages to finish loading, unnecessarily (P1)

**File:** `app/(shell)/chat/[id]/page.tsx:246-248`, `components/chat/DocumentPanel.tsx:32-56`

```tsx
if (isLoading) {
  return <TranscriptSkeleton />        // <-- this early return replaces the WHOLE page,
}                                       //     including <DocumentPanel>, not just the transcript
return (
  <div className="flex-1 flex min-h-0">
    <div className="flex-1 flex flex-col min-h-0">
      <MessageList ... />
      ...
    </div>
    <DocumentPanel conversationId={id} isOpen={isPanelOpen} ... />
  </div>
)
```

`DocumentPanel` has its own independent loading state and calls `GET /conversations/{id}/documents` in a `useEffect` gated only on `isOpen` (default `true`). But because the *entire* conditional return in `ConversationPage` is gated on `isLoading` (from `useMessages`), `DocumentPanel` never mounts — and therefore never starts its fetch — until the messages request has already resolved. Two independent, unrelated requests (`GET /messages`, `GET /documents`) that could run concurrently are instead serialized, adding one full request's latency to when the documents panel becomes usable.

**Proposed fix:** Narrow the `isLoading` skeleton to just the transcript/input column (e.g. wrap `<MessageList>`/`<InputBar>` in the loading check) and always render `<DocumentPanel>` in the same pass, so its fetch fires on the same tick as `useMessages`' fetch instead of after it.

---

## 3. Every data hook resolves its own Clerk token independently (P2 — verify before investing)

**Files:** `hooks/useConversations.tsx:51`, `hooks/useMessages.ts:37`, `components/chat/DocumentPanel.tsx:38`

`useConversations`, `useMessages`, and `DocumentPanel` each independently `await getToken()` immediately before their own `fetch`, rather than resolving a token once at a shared boundary and passing it down. Clerk caches short-lived JWTs client-side, so this likely does **not** cause duplicate network calls to Clerk in the common case — but each hook still pays a promise-hop (`await getToken()` then `await fetch()`) serially rather than having the token ready when the fetch is issued.

**Recommendation:** Check the Network tab for repeated calls to Clerk's token endpoint on a single page load before treating this as a real problem. If Clerk is in fact deduping, this is not worth the refactor.

---

## 4. No bundle analyzer configured — bundle-size claims below are inference, not measurement (P2)

There is no `next.config.js`/`.mjs` in `frontend/`, and `@next/bundle-analyzer` is not in `devDependencies`. All bundle-size observations in this report are from reading imports, not from an actual build trace. Before spending effort on bundle-size work, wire up `@next/bundle-analyzer` (or `ANALYZE=true next build`) to get real per-route JS sizes.

---

## What's already fine (no action needed)

- **Recharts is correctly code-split.** `recharts` is imported in exactly one file, `components/chat/ChartBlock.tsx`. Its only consumer, `components/chat/MessageBubble.tsx:14`, already lazy-loads it via `next/dynamic(() => import("./ChartBlock"), { ssr: false })`, per the comment referencing commit `75845f0` ("only generate charts when the user explicitly asks"). Recharts will not appear in the initial JS payload for any route — it's fetched only when a message actually carries `chart_data`. Just don't regress this by importing `ChartBlock` directly anywhere else (verified: nothing else does today).
- **`react-markdown` + `remark-gfm` eager import in `MessageBubble.tsx` is legitimate weight**, not a "never renders" case like Recharts — every assistant message on both chat pages needs markdown rendering, so lazy-loading it would just move the cost to first-message-render with no real savings.
- **Server/client boundaries are drawn correctly.** `app/layout.tsx` and `app/(shell)/layout.tsx` are both Server Components (no `"use client"`); the shell layout does only an `auth()` check + `redirect()` and renders `<Sidebar/>` (client) around `{children}`. No server-only weight (DB clients, node-only libs) is being pulled into the client bundle. `ChatPage` and `ConversationPage` are `"use client"` wholesale, which is appropriate given they're almost entirely stateful/interactive — there's no obvious server/client split to extract without a larger data-fetching refactor (see #1).
- **`lucide-react` icon imports are all named ESM imports** (`import { X } from "lucide-react"`), which the package supports tree-shaking for. No barrel-style `import * as Icons` usage found anywhere.
- **`useMessages().load(id)` and the sidebar's conversation-list load already run in parallel** on the conversation page — both fire from independent `useEffect`s on mount, so that part of the waterfall is already as good as it can be without a combined backend endpoint.
- **Dead code, not a bundle problem:** `components/portfolio/*.tsx` and the memories page UI are unreachable — both `app/(shell)/portfolio/page.tsx` and `app/(shell)/memories/page.tsx` are stub `redirect("/chat")`s (per their own comments, real UI reverted at commit `bd6ffda`). Next's per-route code splitting already excludes these from every live route's bundle since nothing imports them anymore. Worth deleting for repo hygiene, but not a performance finding.
