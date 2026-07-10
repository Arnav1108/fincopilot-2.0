# Prioritized UI Backlog — FinCopilot 2.0

**Date:** 2026-07-08 · **Input:** fresh UI/UX audit of `frontend/` at commit `6e41d8a` (i.e. *after* the frontend-audit fixes landed — this report does not re-list resolved findings from `frontend-audit.md`).
**Method:** static code audit of every file under `app/`, `components/`, `hooks/`, `lib/`, plus Tailwind/shadcn/Clerk configuration. No browser session was run; a visual pass on a live deploy is recommended to confirm the handful of items marked (NV).
**Lens:** this is a portfolio project — "Impact" is scored against *"would a senior frontend engineer or recruiter be impressed in the first 10 seconds"* plus daily-use friction, not production risk.

**Scoring:** Impact 1–5 (how much it hurts the impression/UX) · Risk 1–5 (how likely a viewer or user actually hits it) · Effort 1–5 (5 = hardest). **Priority = (Impact + Risk) × (6 − Effort)** — same formula as `PRIORITIZED-BACKLOG.md`, so a painful, visible, *easy* fix outranks a painful, visible, hard one.

All paths relative to `frontend/`.

---

## Scope notes — read before filing bugs

- **Portfolio and Memories are intentionally hidden** from nav (`app/(shell)/portfolio/page.tsx` and `memories/page.tsx` are deliberate redirect stubs). Their absence is **not** flagged. The parked `components/portfolio/*` files and the `ui/table`/`ui/input` primitives they hold alive are treated as intentionally preserved, not dead code.
- **`MessageBubble`, `InputBar`, `ChartBlock`, `useStream` are preserve/reuse components** — every finding against them proposes incremental changes, never rewrites.
- The Playwright-accommodation complexity in `InputBar.tsx:64-123` (filesRef mirror, native change listener) is documented and intentional — not flagged.

## What's already good (preserve this)

The previous audit round clearly landed: `MessageBubble` is memoized with a correct rationale comment; the stream lifecycle is solid (abort on unmount/switch, partial kept on Stop, reload on error, EOF-without-done handled); conversation delete uses a real `AlertDialog`; icon-only buttons consistently carry `aria-label` + `title`; there's an `aria-live` region announcing agent progress; markdown financial tables right-align/monospace numeric cells and color only explicitly-signed values with a WCAG 1.4.1 note; `ChartBlock` is lazy-loaded behind a skeleton with an error boundary; error/loading/not-found route files all exist. The **AgentStatus pipeline stepper** (Routing → Selecting tool → Planning → Executing → Synthesizing) is the most distinctive UI element in the app — it shows the agent architecture off and should be kept and polished, not replaced.

The problems below are almost all *polish and identity*, not correctness.

---

## TLDR — the "first 10 seconds" list

Five fixes, none bigger than a small PR, that account for most of the gap between "works" and "impressive":

1. **The app's fonts don't work.** `layout.tsx:6-15` loads Geist Sans + Geist Mono and sets their CSS variables — but `tailwind.config.ts` never maps them into `fontFamily`, and `<body>` never applies them. The entire app renders in the OS default stack (Segoe UI on Windows) while shipping two font files that do nothing. One `fontFamily: { sans: [...], mono: [...] }` block fixes typography app-wide, including the `font-mono` financial table cells which currently don't use Geist Mono either.
2. **All modal animations are silently dead.** `ui/dialog.tsx`, `ui/alert-dialog.tsx`, `ui/select.tsx` are covered in `data-[state=open]:animate-in fade-in zoom-in` classes — but `tailwindcss-animate` is not in `package.json` and `tailwind.config.ts:75` has `plugins: []`. Every dialog/select pops in with zero transition. `npm i tailwindcss-animate` + one line in plugins.
3. **The first screen a recruiter sees is a dead end.** A fresh account landing on `/chat` gets one line of small muted text — *"Ask me anything about a company, filing, or market event."* — **with no input bar and no button** (`app/(shell)/chat/page.tsx:25-33`). You literally cannot ask it anything from the screen that invites you to. Replace with a designed empty state: wordmark, 3–4 clickable example-prompt cards ("Compare NVDA and AMD's latest 10-Ks", …) that create a conversation and send, and either an InputBar or auto-created conversation.
4. **The auth flow is bolted-on.** Sign-in/sign-up render a bare, light-themed Clerk card on `bg-slate-50` (`app/sign-in/[[...sign-in]]/page.tsx:5`) — a white flash between two screens of a forced-dark app, with zero FinCopilot branding. Dark background + Clerk `appearance`/dark baseTheme + a wordmark and one-line tagline makes it feel like one product. Folding the fragile `.cl-*` `!important` overrides (`globals.css:69-96`, hardcoded `#94a3b8`) into the same `appearance` API is part of the same PR.
5. **The theme is literally colorless.** Every `.dark` token in `globals.css:29-50` is a zero-saturation gray — even `--primary` is plain white. There is no brand hue anywhere in the system, so each component invented its own: orange stepper, blue ingestion banner, yellow/blue/emerald/red status chips, amber warnings, six hardcoded hex chart colors. The quick version of the fix — pick one accent (a restrained financial teal/blue), set it as `--primary`/`--ring`, use it for the stepper, active states, and send button — transforms the first impression for effort ~2. The full tokenization is Phase 2 work (see D1).

---

## Prioritized findings

Sorted by priority score. Category: Identity / Polish / UX-flow / Responsive / A11y / Data-viz / Hygiene.

| Priority | Finding | File(s) | Category | Impact | Risk | Effort |
|---:|---|---|---|:-:|:-:|:-:|
| 45 | Geist Sans/Mono loaded via `next/font/local` but never applied — no `fontFamily` mapping in Tailwind, no `font-sans` on body; whole app renders in OS default while shipping both woffs; `font-mono` utilities (tables, code) fall back to Tailwind's stock mono stack | `app/layout.tsx:6-15,29`, `tailwind.config.ts:10-74` | Identity | 4 | 5 | 1 |
| 36 | First-run empty state is a dead end: one line of muted `text-sm`, no InputBar, no CTA, no example prompts — the invitation to "ask me anything" has nowhere to type; `MessageList` empty state (new conversation) is the same single sentence | `app/(shell)/chat/page.tsx:25-33`, `components/chat/MessageList.tsx:73-78` | UX-flow | 4 | 5 | 2 |
| 35 | `tailwindcss-animate` missing (not a dependency, `plugins: []`) — every `animate-in/out`, `fade-*`, `zoom-*`, `slide-*` class in dialog/alert-dialog/select silently no-ops; all modals pop with zero transition | `tailwind.config.ts:75`, `package.json`, `components/ui/*.tsx` | Polish | 3 | 4 | 1 |
| 32 | Auth pages break the product: light `bg-slate-50` + default light Clerk widget in a forced-dark app, no logo/wordmark/tagline — plus the existing Clerk theming is split across fragile `.cl-*` `!important` CSS (hardcoded `#94a3b8`) and a per-component `appearance` object with more hardcoded hexes; consolidate on Clerk's dark `baseTheme` + CSS-var `appearance` | `app/sign-in/[[...sign-in]]/page.tsx:5`, `app/sign-up/[[...sign-up]]/page.tsx:5`, `app/globals.css:69-96`, `components/sidebar/Sidebar.tsx:11-31` | Identity | 4 | 4 | 2 |
| 30 | Waiting-for-first-token indicator is a static `"…"` string — no pulse, no animated dots, no shimmer; the one moment every single message send stares at is inert (the AgentStatus stepper beside it helps, but only renders once the first `node_update` arrives) | `components/chat/MessageBubble.tsx:147,172-173` | Polish | 2 | 4 | 1 |
| 28 | Loading states are the word "Loading…" in three places (route fallback, chat page, sidebar) — no skeletons anywhere except ChartBlock's; sidebar especially begs for 5–6 shimmer rows, transcript for 2–3 bubble skeletons | `app/(shell)/loading.tsx`, `app/(shell)/chat/[id]/page.tsx:226-232`, `components/sidebar/Sidebar.tsx:157` | Polish | 3 | 4 | 2 |
| 27 | No color system: `.dark` palette is 100% desaturated (`--primary` = white), so components improvise — orange stepper (`AgentStatus.tsx:75-89`), light-blue ingestion banner (`DocumentIngestionBanner.tsx:21-30` — light palette in a dark app, sub-AA), yellow/blue/emerald/red chips (`DocumentPanel.tsx:19-22`), amber file warnings (`InputBar.tsx:209`), six hex chart colors (`ChartBlock.tsx:21`). Define brand accent + semantic status tokens (`--success/--warning/--info` or chart/status vars) and migrate the six call sites | `app/globals.css:29-50` + files listed | Identity | 5 | 4 | 3 |
| 25 | No scroll-to-bottom affordance: sticky auto-scroll correctly disengages when the user scrolls up during a stream, but nothing offers the way back — new tokens accumulate invisibly below with no "jump to latest" pill or unread hint | `components/chat/MessageList.tsx:44-54` | UX-flow | 2 | 3 | 1 |
| 24 | Desktop-only shell: fixed `w-[260px]` sidebar + message column + `w-72` DocumentPanel with **zero** authored `md:`/`lg:` breakpoints; on a phone the three panes squeeze rather than reflow, and touch targets are ~28px (`p-1.5` icon buttons) vs the 44px guideline. Needs: sidebar → off-canvas drawer below `md`, DocumentPanel → overlay/sheet, InputBar `enterKeyHint="send"` + larger tap targets | `app/(shell)/layout.tsx`, `components/sidebar/Sidebar.tsx:70,113`, `components/chat/DocumentPanel.tsx`, `components/chat/InputBar.tsx` | Responsive | 4 | 4 | 3 |
| 20 | Sidebar create/rename/delete failures vanish into `catch {}` — no toast system exists, so a failed rename just… reverts silently; add one lightweight toast primitive (e.g. sonner) and surface these three plus confirmation-send failures | `components/sidebar/Sidebar.tsx:44-65`, `app/(shell)/chat/[id]/page.tsx:136-140` | UX-flow | 3 | 2 | 2 |
| 20 | Post-confirmation outcome invisible (NV): answering the filing-download banner drains the ack stream as `res.text()` but never reloads messages or re-opens a stream — whatever the backend does next (ingestion, answer) doesn't render until the user navigates or sends again | `app/(shell)/chat/[id]/page.tsx:118-143` | UX-flow | 3 | 2 | 2 |
| 20 | Live UI bypasses its own design system: chat + sidebar hand-roll every `<button>`/`<input>` with repeated 6-class strings and a second focus convention (`focus:ring-1 ring-border` on search vs shadcn's `focus-visible:ring-2`); `ui/button` + `ui/input` exist and are used only by parked portfolio code — adopt them (or extract the repeated icon-button recipe) so spacing/focus/disabled states stop drifting | `components/sidebar/Sidebar.tsx:72-135,148`, `components/chat/InputBar.tsx:268-340`, `components/ui/button.tsx` | Hygiene | 2 | 3 | 2 |
| 20 | Conversation rows are `div[role=button]` + `router.push` — keyboard works (previous audit fixed that) but they're still not links: no middle-click/ctrl-click/new-tab, no copy-link, invisible to "open in new tab" muscle memory; also no `<nav>` landmark around the list and no skip-link | `components/sidebar/ConversationItem.tsx:70-92`, `components/sidebar/Sidebar.tsx:154` | A11y | 2 | 3 | 2 |
| 20 | Enter-to-send fires during IME composition — CJK users submit half-composed text; guard `handleKeyDown` with `e.nativeEvent.isComposing` | `components/chat/InputBar.tsx:155-161` | A11y | 2 | 2 | 1 |
| 20 | ChartBlock edge cases: `data.series: []` renders an empty chart frame with axes and nothing else (only `!data` is guarded — add a "No data to chart" state); line/bar charts have no `CartesianGrid`, making value reading against the 300px plot harder; hex palette ties into D1 tokenization | `components/chat/ChartBlock.tsx:21,54,77-197` | Data-viz | 2 | 2 | 1 |
| 20 | Zero share-surface metadata: no OpenGraph/Twitter tags, no OG image, wordmark is a plain `text-sm` span (collapsed sidebar shows no brand at all) — for a portfolio project the link preview in Slack/LinkedIn *is* a first-impression surface | `app/layout.tsx:17-20`, `components/sidebar/Sidebar.tsx:116` | Identity | 2 | 2 | 1 |
| 15 | Hygiene bundle: dead `setTitle` context method (defined, exported, never consumed); empty toolbar gap where a control was removed in InputBar; unused `SelectSeparator/Label/Group/ScrollButton` exports; `@radix-ui/react-separator` dep with no component; `pages/**` content glob for a dir that doesn't exist; webkit-scrollbar recipe duplicated verbatim in two files (extract a utility) | `hooks/useConversations.tsx:104-106,124`, `components/chat/InputBar.tsx:311-312`, `components/ui/select.tsx`, `package.json:19`, `tailwind.config.ts:6`, `Sidebar.tsx:154` + `MessageList.tsx:66` | Hygiene | 1 | 2 | 1 |
| 15 | Code blocks render as plain `<pre>` with no syntax highlighting — low priority for a finance tool, but agent answers do emit fenced blocks; `rehype-highlight` or shiki is a contained change to the existing `code` renderer | `components/chat/MessageBubble.tsx:205-213` | Polish | 1 | 2 | 1 |

**Deliberate non-findings:** dark-only theming is a legitimate product choice — the bug is the *inconsistency* (light auth pages, light ingestion banner), not the absence of a toggle. Recommend committing to dark-only explicitly: fix the two inconsistent surfaces (scored above), and optionally delete or comment the never-activated `:root` light block so the intent is documented. Likewise `DEFAULT_MODEL` being fixed with no picker UI is a product decision, not a UI defect.

---

## Quick wins vs. design-system work

The user-facing split requested — what ships in an afternoon vs. what needs design thinking.

### Quick wins (each ≤ half a day; together they close most of the impression gap)

| # | Fix | Effort |
|---|---|:-:|
| Q1 | Map Geist into `fontFamily` (sans + mono) and verify tables/code pick up Geist Mono | 1 |
| Q2 | `npm i tailwindcss-animate` + register plugin — dialogs animate again | 1 |
| Q3 | Animated thinking indicator (pulsing dots or blinking cursor) replacing static `"…"` | 1 |
| Q4 | Quick accent pass: one brand hue into `--primary`/`--ring`, reuse in stepper + send button | 1–2 |
| Q5 | Dark, branded auth pages: bg token + Clerk dark `appearance` + wordmark/tagline; delete the `.cl-*` hex overrides in the same PR | 2 |
| Q6 | Designed empty state with 3–4 clickable example-prompt cards that create-and-send | 2 |
| Q7 | Scroll-to-bottom pill when auto-scroll is disengaged during streaming | 1 |
| Q8 | Skeleton rows for sidebar + transcript loading (replaces three "Loading…" strings) | 2 |
| Q9 | ChartBlock empty-series message + `CartesianGrid` | 1 |
| Q10 | OG/Twitter metadata + OG image; brand mark in collapsed sidebar | 1 |
| Q11 | IME composition guard on Enter-to-send | 1 |

### Design-system-level work (needs a decision or a real design pass)

| # | Work | Effort | Notes |
|---|---|:-:|---|
| D1 | **Color identity pass**: brand accent + semantic status tokens (`success/warning/info`, chart series vars) in `globals.css` + Tailwind config; migrate AgentStatus, DocumentIngestionBanner, DocumentPanel chips, InputBar warnings, ChartBlock palette onto them. Q4 is the down-payment; this is the loan | 3 | The single biggest "generic template → real product" lever |
| D2 | **Responsive shell**: off-canvas sidebar (sheet/drawer) below `md`, DocumentPanel as overlay, 44px touch targets, `enterKeyHint`/`inputMode` on the textarea, viewport-driven collapse instead of state-only | 3 | Recruiters open links on phones; currently desktop-only |
| D3 | **Feedback system**: one toast primitive; wire sidebar CRUD failures, confirmation-send failures, and future mutations through it | 2 | Unblocks surfacing every currently-swallowed error |
| D4 | **Primitive consolidation**: adopt `ui/button`/`ui/input` (or extracted icon-button variant) across Sidebar/InputBar/DocumentPanel; one focus-ring convention | 2 | Do alongside D1 so class churn happens once |
| D5 | **Navigation semantics**: conversation rows → real `<Link>`s inside `<nav>`, skip-link, `<header>` on sidebar/panel headers | 2 | Pairs naturally with D2's drawer refactor |
| D6 | **Confirmation flow UX**: route the post-confirmation stream through `useStream` (or reload + poll) so the outcome renders live | 2 | Verify backend stream shape first (NV) |

---

## Suggested sequencing

- **Phase 1 — the afternoon of polish (Q1–Q11):** all effort 1–2, no design decisions harder than picking an accent hue. After this phase the app has real typography, motion, a branded auth flow, a first screen that invites use, and visible streaming affordances. This is the highest ROI-per-hour work available in the repo.
- **Phase 2 — identity & reach (D1, D2, D3):** the color-token system, the responsive shell, and the toast layer. These three decide whether the app reads as "shadcn tutorial" or "designed financial product." D1 before D2 — reflowing panes is easier when the palette is settled.
- **Phase 3 — opportunistic (D4, D5, D6, hygiene + syntax highlighting):** fold into whichever Phase 2 PR touches the same files; none is worth a standalone context switch.

**Explicitly not planned:** light-mode toggle (commit to dark-only instead), Portfolio/Memories nav entries (intentionally hidden), rewrites of MessageBubble/InputBar/ChartBlock/useStream (preserve-and-extend only), replacing the AgentStatus stepper (it's the app's best feature — Phase 2's D1 should make it *more* prominent, not less).
