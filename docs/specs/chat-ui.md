# Spec: Chat UI

## Goal
Build the primary chat interface for FinCopilot — a Claude-like conversational UI where authenticated financial analysts send text questions and receive streamed AI responses with agent status and source citations.

## Background
The backend is fully implemented: conversation CRUD (`POST/GET/PATCH/DELETE /api/v1/conversations`), message history (`GET /api/v1/conversations/{id}/messages`), and SSE streaming (`POST /api/v1/conversations/{id}/stream`). The frontend has a placeholder `/chat` page with Clerk auth but no UI. shadcn/ui is configured (slate base, CSS variables) and lucide-react is installed. No chat components, API client, state management, or SSE consumer exist yet.

---

## Scope

### In scope
- Left sidebar: conversation list grouped by date, new-chat button, double-click-to-rename title, delete conversation, settings link
- Main chat area: message history with user and assistant bubbles, agent status indicator during streaming, source citations below each assistant message, auto-scroll to bottom
- Input bar: textarea (Enter to send, Shift+Enter for newline), disabled send button while streaming, disabled file-upload button (placeholder), disabled SEC filing button (placeholder), disabled peer comparison button (placeholder)
- SSE streaming consumer using native browser `fetch` (not `EventSource`, not a library), rendering tokens progressively
- Agent status cycling (Routing → Planning → Executing → Synthesizing) shown inline during streaming, disappearing after `done`
- Settings modal: view and edit analyst profile (sectors, tracked tickers, investment style, preferred output format, custom context)
- Conversation title rename via double-click on sidebar item (inline edit, blur or Enter to save)
- Page refresh resilience: reload `/chat` shows selected conversation; sidebar lists all conversations
- URL routing: `/chat` → redirect to most recent conversation or empty state; `/chat/[id]` → load that conversation
- Dark mode only (no toggle)
- TypeScript types for all API contracts

### Out of scope
- Right panel of any kind
- File upload (button exists, disabled)
- Real SEC filing fetch (button exists, disabled)
- Peer comparison (button exists, disabled)
- Voice input
- Mobile layout optimization
- Dark/light mode toggle
- Conversation search
- Conversation export
- Regenerate last message button
- Copy button on messages
- Markdown syntax highlighting (render as plain text)
- Any animation beyond simple CSS transitions (no framer-motion)
- Real LLM integration (backend returns hardcoded demo response)

---

## User flow

### Happy path — new conversation
1. User signs in via Clerk, lands on `/chat`.
2. App fetches conversation list (`GET /api/v1/conversations`). If list is empty, show empty state ("Start a new conversation"). If non-empty, redirect to `/chat/[most-recent-id]`.
3. User clicks "New Chat" button in sidebar. App calls `POST /api/v1/conversations`, gets back a `ConversationRead` object, navigates to `/chat/[new-id]`, and adds conversation to top of sidebar list.
4. User types a question in the textarea and presses Enter (or clicks Send).
5. Send button becomes disabled. Input clears. User message bubble appears immediately in the message list.
6. App calls `POST /api/v1/conversations/{id}/stream` with `{ conversation_id, message }` and starts reading the SSE stream via `fetch` + `ReadableStream`.
7. On `node_update` event: show agent status badge ("Routing...", "Planning...", etc.) below the user message.
8. On `tool_call` event: no visual change (logged to console only).
9. On `sources` event: store sources; render them below the assistant message after streaming ends.
10. On `token` event: append token to the assistant message bubble, which grows in place.
11. On `done` event: hide agent status badge, show source citations below the assistant message, re-enable send button.
12. Page title updates to match conversation title.

### Returning to an existing conversation
1. User clicks a conversation in the sidebar. App navigates to `/chat/[id]`.
2. App fetches `GET /api/v1/conversations/{id}/messages` and renders full message history.
3. Sources stored in `Message.agent_trace` JSONB are not surfaced (the backend does not return them in `MessageRead`). Sources only appear for messages received in the current session.
4. Auto-scroll to bottom on load.

### Renaming a conversation
1. User double-clicks a conversation title in the sidebar. Title text becomes an inline `<input>`.
2. User edits and presses Enter or blurs the field.
3. App calls `PATCH /api/v1/conversations/{id}` with `{ title }`. On success, sidebar updates.
4. If the input is empty or whitespace, discard and restore previous title without calling the API.
5. If the API returns an error, restore previous title and show a brief toast.

### Deleting a conversation
1. User hovers a sidebar item — a delete (trash) icon appears.
2. User clicks the icon. App calls `DELETE /api/v1/conversations/{id}`.
3. On 204, remove item from sidebar. If the deleted conversation was active, navigate to the next conversation in the list or show empty state.

### Settings
1. User clicks the Settings icon at the bottom of the sidebar.
2. A modal opens showing the analyst profile form. App fetches `GET /api/v1/profile`.
3. Form fields: sectors (tag input), tracked tickers (tag input, uppercase only), investment style (select: growth/value/blend), preferred output format (select: concise/detailed/bullet_points), custom context (textarea, max 500 chars).
4. User edits and clicks Save. App calls `PUT /api/v1/profile` with changed fields only. On success, close modal.
5. Validation errors from the API are shown inline next to the relevant field.

### Error states
- Network error during streaming: show an error message in place of the assistant response; re-enable send button.
- 404 when loading `/chat/[id]` (conversation deleted or doesn't belong to user): show "Conversation not found" and navigate to `/chat`.
- 401 on any API call: Clerk handles re-auth; the `useAuth()` token is always fresh.

---

## Detailed requirements

### Layout
1. The layout is a full-viewport two-column split: fixed 260px sidebar on the left, fluid main area on the right.
2. The sidebar never overlaps the main area (no drawer/overlay on desktop).
3. The app uses dark mode exclusively; no light-mode styles.

### Sidebar
4. The sidebar header contains a "FinCopilot" wordmark/logo and a "New Chat" button (pencil-and-square icon).
5. Conversations are listed below the header, grouped by date into labeled sections: "Today", "Yesterday", "Previous 7 Days", "Previous 30 Days", "Older". Groups with no conversations are omitted.
6. Within each group, conversations are sorted by `updated_at` descending.
7. The active conversation is visually distinguished (highlighted background) from others.
8. A conversation item shows: truncated title (single line, ellipsis), hover state, double-click triggers rename, hover reveals trash icon for delete.
9. Inline rename: on double-click, the title becomes an `<input>` pre-filled with the current title. Enter or blur saves. Escape cancels.
10. The sidebar footer contains a Settings icon button (gear icon). Clicking opens the Settings modal.

### Message list
11. User messages are right-aligned, assistant messages are left-aligned.
12. Each assistant message renders as plain text (no markdown parsing in this phase).
13. Agent status badge appears directly below the user message bubble during streaming, showing "node: status" (e.g., "Synthesizing..."). It disappears when the `done` event is received.
14. Source citations appear below the assistant message bubble after the `done` event. Each citation is a clickable link (`<a target="_blank" rel="noopener noreferrer">`) showing the source title.
15. The message list auto-scrolls to the bottom when a new message is added and during streaming, unless the user has manually scrolled up (scroll-lock detection).
16. When the conversation is loading (fetching messages), show a skeleton or spinner.
17. Empty conversation state (no messages yet) shows a centered prompt: "Ask me anything about a company, filing, or market event."

### Input bar
18. The textarea accepts multi-line input. Enter sends the message. Shift+Enter inserts a newline.
19. The textarea auto-grows up to 5 lines before scrolling internally.
20. Send button is disabled when: input is empty or whitespace, or a stream is in progress.
21. File upload, SEC filing, and peer comparison icon buttons are rendered but permanently disabled with `disabled` attribute and `cursor-not-allowed` styling. No tooltip or explanation needed.
22. Input bar and all its buttons are disabled during streaming.

### SSE streaming
23. Streaming is implemented with native `fetch` + `response.body.getReader()`. No third-party streaming library. No `EventSource`.
24. The `Authorization: Bearer <token>` header is included on every request. The token is retrieved from Clerk's `useAuth()` hook via `getToken()`.
25. SSE lines are parsed manually: split on `\n\n`, extract `event:` and `data:` fields, parse `data` as JSON.
26. `node_update` events update the agent status badge with the `node` field value.
27. `token` events append the `token` field value to the current assistant message content.
28. `sources` events store the sources array; they are rendered after the `done` event.
29. `tool_call` events are consumed and logged to `console.debug`; no UI change.
30. `done` event: hide agent status badge, render sources, re-enable input.
31. If the stream is interrupted (network error, non-200 status), mark the assistant message as failed and re-enable the input.

### Settings modal
32. The Settings modal is a centered dialog overlay with a close button (X) and an explicit Save button.
33. The sectors and tracked_tickers fields are tag inputs: user types a value and presses Enter or comma to add a tag; tags are individually removable with an X.
34. Tracked tickers are auto-uppercased as the user types. The API validator pattern is `^[A-Z]{1,10}$` — the form enforces this client-side (1–10 uppercase letters only) before submitting.
35. Investment style dropdown options: "Growth", "Value", "Blend" (maps to `growth`, `value`, `blend`).
36. Preferred output format dropdown options: "Concise", "Detailed", "Bullet Points" (maps to `concise`, `detailed`, `bullet_points`).
37. Custom context is a textarea with a live character count showing `n/500`.
38. Save calls `PUT /api/v1/profile` with only the fields present in `ProfileUpdate`. If the profile has never been saved, the GET returns an auto-created default profile — form initializes from it.
39. Save button shows a loading state while the request is in flight.
40. On success, close modal. On API error, show the error message from the response body.

### Routing
41. `/chat` fetches the conversation list. If non-empty, redirects to `/chat/[most-recent-id]`. If empty, renders the empty state without redirecting.
42. `/chat/[id]` is the canonical URL for a conversation. Refreshing the page re-fetches the conversation and its messages.
43. URL updates when the user switches conversations or creates a new one (Next.js `router.push`).

### Type safety
44. All API response shapes are defined as TypeScript interfaces in `frontend/lib/types.ts` matching the Pydantic schemas exactly.
45. SSE event payloads are typed as a discriminated union on the `event` field.
46. No `any` types in new code.

---

## Data model changes

No backend data model changes. All required tables and columns already exist.

---

## API contracts

All API contracts are implemented in the backend. This section documents them as the frontend will consume them.

### `POST /api/v1/conversations`
- Auth: required (Bearer JWT)
- Request body: none
- Response 201: `ConversationRead { id: UUID, title: string, created_at: datetime, updated_at: datetime }`

### `GET /api/v1/conversations`
- Auth: required
- Response 200: `ConversationRead[]` ordered by `updated_at` desc

### `PATCH /api/v1/conversations/{id}`
- Auth: required
- Request body: `{ title: string }` (1–255 chars, stripped)
- Response 200: `ConversationRead`
- Response 404: conversation not found or not owned by user

### `DELETE /api/v1/conversations/{id}`
- Auth: required
- Response 204: no body
- Response 404: conversation not found or not owned by user

### `GET /api/v1/conversations/{id}/messages`
- Auth: required
- Response 200: `MessageRead[] { id, conversation_id, role: "user"|"assistant", content, created_at }` ordered by `created_at` asc
- Response 404: conversation not found or not owned by user

### `POST /api/v1/conversations/{id}/stream`
- Auth: required
- Request body: `{ conversation_id: UUID, message: string }` (max 10 000 chars, not blank)
- Response: `text/event-stream`
- SSE event types:
  - `node_update`: `{ node: string, status: "running" }`
  - `tool_call`: `{ tool: string, input: object }`
  - `sources`: `{ sources: { title: string, url: string }[] }`
  - `token`: `{ token: string }`
  - `done`: `{ message_id: string | null }`
- Response 400: `conversation_id` mismatch
- Response 404: conversation not found

### `GET /api/v1/profile`
- Auth: required
- Response 200: `ProfileRead { id, user_id, sectors: string[], tracked_tickers: string[], investment_style: "growth"|"value"|"blend"|null, preferred_output_format: "concise"|"detailed"|"bullet_points"|null, custom_context: string|null, created_at, updated_at }`

### `PUT /api/v1/profile`
- Auth: required
- Request body: `ProfileUpdate` (all fields optional): `{ sectors?, tracked_tickers?, investment_style?, preferred_output_format?, custom_context? }`
- Response 200: `ProfileRead`
- Response 422: validation error (ticker format, list lengths, context length)

---

## Component and file structure

### New files — frontend

**`frontend/lib/types.ts`**
TypeScript interfaces for all API response types (`ConversationRead`, `MessageRead`, `ProfileRead`, `ProfileUpdate`, `SseEvent` discriminated union).

**`frontend/lib/api.ts`**
Thin fetch wrapper: adds `Authorization: Bearer` header, sets `Content-Type: application/json`, throws typed errors on non-2xx. Exports named functions per endpoint (`createConversation`, `listConversations`, `renameConversation`, `deleteConversation`, `listMessages`, `getProfile`, `updateProfile`).

**`frontend/hooks/useConversations.ts`**
React state + actions for the conversation list: fetch on mount, optimistic add/rename/delete.

**`frontend/hooks/useMessages.ts`**
Fetch and hold message history for a given `conversationId`.

**`frontend/hooks/useStream.ts`**
Encapsulates the SSE streaming logic: `fetch` + `ReadableStream` reader, SSE line parser, returns `{ stream, isStreaming, agentStatus, sources, error }`. Accepts a callback for each `token` event.

**`frontend/app/chat/layout.tsx`**
Persistent layout for all `/chat/*` routes: renders `<Sidebar>` on the left and `{children}` on the right. Fetches conversation list once here so it's available to both the sidebar and child pages.

**`frontend/app/chat/page.tsx`** _(modify existing placeholder)_
Redirect to most recent conversation or render empty state.

**`frontend/app/chat/[id]/page.tsx`**
Conversation page: fetches messages for `params.id`, renders `<MessageList>` and `<InputBar>`.

**`frontend/components/sidebar/Sidebar.tsx`**
Root sidebar component. Renders header, conversation groups, footer.

**`frontend/components/sidebar/ConversationItem.tsx`**
Single sidebar row: title, hover delete button, double-click rename (inline input).

**`frontend/components/sidebar/ConversationGroup.tsx`**
Date group label + list of `ConversationItem`.

**`frontend/components/chat/MessageList.tsx`**
Scrollable message list. Auto-scroll logic. Renders `MessageBubble` for each message, plus the streaming assistant bubble if `isStreaming`.

**`frontend/components/chat/MessageBubble.tsx`**
Renders a single message (user or assistant). For assistant: shows agent status badge during streaming, source citations after `done`.

**`frontend/components/chat/AgentStatus.tsx`**
Small badge showing current agent node name with animated dots ("Synthesizing...").

**`frontend/components/chat/SourceList.tsx`**
Renders the source citations as a list of external links below an assistant message.

**`frontend/components/chat/InputBar.tsx`**
Textarea + send button + disabled icon buttons. Handles Enter/Shift+Enter. Calls `onSend(message)` prop.

**`frontend/components/settings/SettingsModal.tsx`**
Dialog overlay with profile form. Fetches profile on open, calls `updateProfile` on save.

**`frontend/components/settings/TagInput.tsx`**
Reusable tag input component used for sectors and tracked tickers.

### shadcn/ui components to install
- `dialog` (Settings modal)
- `button`
- `textarea`
- `input`
- `select`
- `badge` (agent status, source tags)
- `separator`

Install with: `npx shadcn-ui@latest add dialog button textarea input select badge separator`

### Modified files

**`frontend/app/chat/page.tsx`** — replace placeholder with redirect/empty-state logic.

---

## External dependencies

- **Clerk (`@clerk/nextjs`)**: already installed. `useAuth().getToken()` provides the JWT for API calls. If Clerk cannot return a token, the API call must not be made.
- **shadcn/ui**: already configured. New components installed via CLI into `components/ui/`.
- **lucide-react**: already installed. Icon source for all icons (PencilSquare, Trash2, Settings, Send, Paperclip, etc.).
- No new npm packages should be required. If a tag input component needs to be built, build it from scratch as a small component — do not add a library.

---

## Testing plan

### Manual verification steps (primary verification method for this UI-heavy feature)

1. Sign in → verify redirect to `/chat`. With no conversations, verify empty state text appears.
2. Click New Chat → verify sidebar adds a new entry, URL changes to `/chat/[id]`.
3. Type "What is Apple's revenue?" → press Enter. Verify:
   - User bubble appears immediately.
   - Input clears and disables.
   - Agent status cycles: Routing → Planning → Executing → Synthesizing.
   - Tokens stream in one by one.
   - After `done`: status badge disappears, two source links appear.
   - Input re-enables.
4. Refresh the page at `/chat/[id]`. Verify conversation and messages reload. Sources are not shown (not stored in `MessageRead`).
5. Double-click a sidebar title → verify inline input appears, pre-filled. Type new name, press Enter → verify sidebar updates.
6. Press Escape during rename → verify original title restored.
7. Hover a sidebar item → verify trash icon appears. Click → verify item removed, navigate away if active.
8. Click Settings → verify modal opens, profile form loads. Edit a field, click Save → verify modal closes.
9. Enter an invalid ticker (lowercase or >10 chars) in the tickers tag input → verify it is rejected client-side.
10. Open two browser tabs at the same URL. Send a message in one tab. Verify the other tab's sidebar does not auto-update (no polling/websocket in scope).

### Unit tests (optional, not blocking)
- `lib/api.ts`: mock `fetch`, assert correct headers and URL construction for each function.
- `hooks/useStream.ts`: mock a `ReadableStream`, assert SSE events are parsed correctly and callbacks fire in order.
- `TagInput.tsx`: assert tags are added on Enter/comma, removed on X click, tickers are uppercased.

---

## Observability

The backend already emits structured logs for all API calls (`user_message_saved`, `chat_stream_started`, `chat_stream_completed`, `assistant_message_save_failed`).

Frontend-side:
- `console.debug` for each SSE event received (event name + payload), so developers can trace streaming in DevTools.
- `console.error` on stream failure with the error message.
- No custom metrics or traces in this phase.

Healthy state: user sends a message, all five SSE event types are received (node_update × 4, tool_call × 1, sources × 1, token × N, done × 1), assistant message renders fully, sources appear.

Unhealthy state: stream response is non-200 (check Network tab), or `done` event never arrives (stream hangs — check backend process), or `Authorization` header is missing (401 on every request — check `getToken()` call).

---

## Risks and open questions

1. **SSE with `fetch` vs `EventSource`**: `EventSource` does not support custom headers, so `fetch` + `ReadableStream` is required. The manual SSE parser (split on `\n\n`, extract `event:` and `data:`) must handle partial chunks correctly — a chunk boundary may split an event in the middle.

2. **Scroll-lock conflict**: auto-scrolling to the bottom during streaming should pause if the user scrolls up. The heuristic (check if scrollTop + clientHeight ≈ scrollHeight) can misfire on resize. Accept imperfect behavior for now; revisit if it becomes annoying.

3. **Conversation list staleness after send**: after streaming completes, the `updated_at` of the conversation changes on the backend. The sidebar list order should update to reflect this. This requires either re-fetching the list or optimistically re-sorting client-side. Decision: optimistically move the active conversation to the top of the list on `done`.

4. **Token from `getToken()`**: Clerk's `getToken()` is async. The stream request cannot be initiated until the token resolves. If the token fetch takes >200ms, there will be a visible delay before streaming starts. This is acceptable for now.

5. **Sources not persisted in `MessageRead`**: the backend stores the assistant response content in `Message.content` but not the sources (they're implicit in `agent_trace` JSONB, which `MessageRead` does not expose). Sources will not be shown when loading a historical conversation. This is a known limitation and out of scope.

6. **Empty assistant message state**: between the user message appearing and the first `token` event, the assistant bubble exists but has no text. A blinking cursor or ellipsis should indicate loading — but complex animations are out of scope. Use a simple "..." placeholder that disappears when the first token arrives.
