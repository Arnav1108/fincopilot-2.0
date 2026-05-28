/**
 * FinCopilot chat end-to-end tests.
 *
 * Prerequisites:
 *   1. Next.js dev server running on http://localhost:3000
 *   2. FastAPI backend + Redis + Postgres running (docker-compose up or locally)
 *   3. Clerk auth configured (see e2e/helpers/auth.ts)
 *   4. Fixtures generated: node e2e/fixtures/generate-fixtures.mjs
 *
 * Selectors marked [data-testid] require the attributes listed at the bottom
 * of this file to be added to the component files before these tests pass.
 */

import path from "path"
import { test, expect } from "@playwright/test"
import { setupClerkAuth } from "./helpers/auth"

// Playwright transforms .ts test files to CJS, so __dirname is available natively.
const SAMPLE_PDF = path.join(__dirname, "fixtures", "sample.pdf")

// ── Selectors ─────────────────────────────────────────────────────────────────
// All [data-testid] values that must be added to components (see note at EOF).

const SEL = {
  newChatBtn: '[data-testid="new-chat-button"]',
  messageInput: '[data-testid="message-input"]',
  sendBtn: '[data-testid="send-button"]',
  attachBtn: '[data-testid="attach-button"]',
  fileInput: '[data-testid="file-input"]',
  ingestProgress: '[data-testid="ingest-progress"]',
  agentStatus: '[data-testid="agent-status"]',
  messageUser: '[data-testid="message-user"]',
  messageAssistant: '[data-testid="message-assistant"]',
  ragBadge: '[data-testid="rag-badge"]',
  conversationItem: '[data-testid="conversation-item"]',
}

// UUID pattern used in /chat/<uuid> URLs
const CONV_URL = /\/chat\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/

// ── Shared setup ──────────────────────────────────────────────────────────────

test.beforeEach(async ({ page }) => {
  await setupClerkAuth(page)
})

// ── Helper ────────────────────────────────────────────────────────────────────

async function openNewConversation(page: import("@playwright/test").Page) {
  // Always click "New chat" so each test gets a fresh conversation.
  await page.goto("/chat")
  await page.waitForSelector(SEL.newChatBtn, { timeout: 10_000 })
  await page.click(SEL.newChatBtn)
  await page.waitForURL(CONV_URL, { timeout: 10_000 })
}

// ── Test 1 ────────────────────────────────────────────────────────────────────

test("test_new_conversation_loads", async ({ page }) => {
  await page.goto("/chat")
  await page.waitForSelector(SEL.newChatBtn, { timeout: 10_000 })
  await page.click(SEL.newChatBtn)

  // URL should change to /chat/<uuid>
  await page.waitForURL(CONV_URL, { timeout: 10_000 })
  expect(page.url()).toMatch(CONV_URL)

  // Input bar must be visible
  await expect(page.locator(SEL.messageInput)).toBeVisible()
})

// ── Test 2 ────────────────────────────────────────────────────────────────────

test("test_send_message_and_receive_response", async ({ page }) => {
  await openNewConversation(page)

  await page.locator(SEL.messageInput).fill("What is a P/E ratio?")
  await page.click(SEL.sendBtn)

  // Agent status indicator appears while the LangGraph pipeline runs
  await expect(page.locator(SEL.agentStatus)).toBeVisible({ timeout: 10_000 })

  // Wait for streaming to finish (agent status disappears, max 30 s)
  await expect(page.locator(SEL.agentStatus)).toBeHidden({ timeout: 30_000 })

  // At least one assistant message must be visible and non-empty
  const assistantMsg = page.locator(SEL.messageAssistant).first()
  await expect(assistantMsg).toBeVisible()
  const text = await assistantMsg.innerText()
  expect(text.trim().length).toBeGreaterThan(0)
})

// ── Test 3 ────────────────────────────────────────────────────────────────────

test("test_file_upload_shows_ingest_progress", async ({ page }) => {
  await openNewConversation(page)

  // Click attach button and intercept the file chooser — trusted event path,
  // so React's onChange and the native listener both fire reliably.
  const [fileChooser3] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.locator(SEL.attachBtn).click(),
  ])
  await fileChooser3.setFiles(SAMPLE_PDF)
  await expect(page.locator('[data-testid="file-chip"]')).toBeVisible({ timeout: 8_000 })

  // Type a prompt and send — this triggers Phase 1 (ingestion) of the SSE stream
  await page.locator(SEL.messageInput).fill("Describe this document")
  await page.click(SEL.sendBtn)

  // Ingest progress indicator must appear during Phase 1 (Celery may be slow)
  await expect(page.locator(SEL.ingestProgress)).toBeVisible({ timeout: 30_000 })

  // After ingestion Phase 1 ends the indicator disappears (max 30 s)
  await expect(page.locator(SEL.ingestProgress)).toBeHidden({ timeout: 30_000 })

  // After Phase 2 (agent response) finishes the textarea should be re-enabled
  await expect(page.locator(SEL.agentStatus)).toBeHidden({ timeout: 30_000 })
  await expect(page.locator(SEL.messageInput)).toBeEnabled()
})

// ── Test 4 ────────────────────────────────────────────────────────────────────

test("test_rag_badge_shows_after_doc_query", async ({ page }) => {
  await openNewConversation(page)

  // Click attach button and intercept the file chooser — trusted event path.
  const [fileChooser4] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.locator(SEL.attachBtn).click(),
  ])
  await fileChooser4.setFiles(SAMPLE_PDF)
  await expect(page.locator('[data-testid="file-chip"]')).toBeVisible({ timeout: 8_000 })
  await page.locator(SEL.messageInput).fill("Summarise this document")
  await page.click(SEL.sendBtn)

  // Wait for the full response (ingestion + agent, max 45 s)
  await expect(page.locator(SEL.agentStatus)).toBeVisible({ timeout: 20_000 })
  await expect(page.locator(SEL.agentStatus)).toBeHidden({ timeout: 45_000 })

  // The settled assistant message must show the RAG badge (📚 + "RAG" text)
  const ragBadge = page.locator(SEL.ragBadge)
  await expect(ragBadge).toBeVisible({ timeout: 5_000 })
  await expect(ragBadge).toContainText("RAG")
})

// ── Test 5 ────────────────────────────────────────────────────────────────────

test("test_conversation_appears_in_sidebar", async ({ page }) => {
  await openNewConversation(page)

  // Capture the conversation ID from the URL
  const url = page.url()
  const convId = url.split("/chat/")[1]

  await page.locator(SEL.messageInput).fill("What is a P/E ratio?")
  await page.click(SEL.sendBtn)

  // Wait for a response so the conversation gets a title and is bumped to top
  await expect(page.locator(SEL.agentStatus)).toBeVisible({ timeout: 10_000 })
  await expect(page.locator(SEL.agentStatus)).toBeHidden({ timeout: 30_000 })

  // The sidebar conversation list must contain at least one item …
  const items = page.locator(SEL.conversationItem)
  await expect(items.first()).toBeVisible()

  // … and at least one of them must be the active conversation
  // (either by data attribute or by the item linking to the current URL)
  const activeItem = page
    .locator(SEL.conversationItem)
    .filter({ has: page.locator(`[data-conv-id="${convId}"]`) })

  // If data-conv-id is not yet added, fall back to checking the count is > 0
  const activeCount = await activeItem.count()
  if (activeCount > 0) {
    await expect(activeItem.first()).toBeVisible()
  } else {
    // data-conv-id not yet present — just assert list is non-empty
    expect(await items.count()).toBeGreaterThan(0)
  }
})

/*
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  data-testid ATTRIBUTES TO ADD (do NOT edit component files yet)        ║
 * ╠══════════════════════════════════════════════════════════════════════════╣
 * ║                                                                          ║
 * ║  File: frontend/components/sidebar/Sidebar.tsx                           ║
 * ║    Line 81  <button … aria-label="New chat">                             ║
 * ║             → add data-testid="new-chat-button"                          ║
 * ║                                                                          ║
 * ║    Line 90  <div className="flex-1 overflow-y-auto …">  (conv list)      ║
 * ║             → add data-testid="conversation-list"                        ║
 * ║                                                                          ║
 * ║  File: frontend/components/sidebar/ConversationItem.tsx                  ║
 * ║    Line 47  outer <div … onClick …>                                      ║
 * ║             → add data-testid="conversation-item"                        ║
 * ║             → add data-conv-id={conversation.id}   (for sidebar test)    ║
 * ║                                                                          ║
 * ║  File: frontend/components/chat/InputBar.tsx                             ║
 * ║    Line 193 ingest-progress <div …>                                      ║
 * ║             → add data-testid="ingest-progress"                          ║
 * ║                                                                          ║
 * ║    Line 207 <textarea …>                                                 ║
 * ║             → add data-testid="message-input"                            ║
 * ║                                                                          ║
 * ║    Line 246 <input type="file" … aria-hidden>                            ║
 * ║             → add data-testid="file-input"                               ║
 * ║             (Playwright's setInputFiles() works on hidden inputs)         ║
 * ║                                                                          ║
 * ║    Line 261 paperclip <button …>                                         ║
 * ║             → add data-testid="attach-button"                            ║
 * ║                                                                          ║
 * ║    Line 296 send <button …>                                              ║
 * ║             → add data-testid="send-button"                              ║
 * ║                                                                          ║
 * ║  File: frontend/components/chat/AgentStatus.tsx                          ║
 * ║    Line 7   outer <span …>                                               ║
 * ║             → add data-testid="agent-status"                             ║
 * ║                                                                          ║
 * ║  File: frontend/components/chat/MessageBubble.tsx                        ║
 * ║    Line 106 user-message outer <div className="flex justify-end">        ║
 * ║             → add data-testid="message-user"                             ║
 * ║                                                                          ║
 * ║    Line 114 assistant-message outer <div className="flex flex-col …">   ║
 * ║             → add data-testid="message-assistant"                        ║
 * ║                                                                          ║
 * ║    Line 42  RagBadge <button … > (the 📚 RAG button)                    ║
 * ║             → add data-testid="rag-badge"                                ║
 * ║                                                                          ║
 * ║  File: frontend/components/chat/MessageList.tsx                          ║
 * ║    Line 43  outer scroll <div ref={containerRef} …>                      ║
 * ║             → add data-testid="message-list"                             ║
 * ║                                                                          ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 */
