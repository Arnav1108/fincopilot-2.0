"use client"

import { memo, useState } from "react"
import dynamic from "next/dynamic"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { cn } from "@/lib/utils"
import type { ChartData, MessageRead, Source, ToolCall } from "@/lib/types"
import AgentStatus from "./AgentStatus"
import SourceList from "./SourceList"

// Recharts is heavy and charts are rare (opt-in since 75845f0) — load it only
// when a message actually has chart data.
const ChartBlock = dynamic(() => import("./ChartBlock"), {
  ssr: false,
  loading: () => (
    <div className="mt-3 animate-pulse rounded-lg border border-border bg-muted/20 p-4">
      <div className="mb-3 h-4 w-40 rounded bg-muted" />
      <div className="min-h-[200px] rounded bg-muted" />
    </div>
  ),
})

// ── RAG badge ─────────────────────────────────────────────────────────────────

interface RagBadgeProps {
  ragUsed: boolean
  relevanceScore?: number | null
  chunkIds?: string[] | null
}

function RagBadge({ ragUsed, relevanceScore, chunkIds }: RagBadgeProps) {
  const [expanded, setExpanded] = useState(false)
  const hasChunks = chunkIds && chunkIds.length > 0

  if (!ragUsed) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
        🧠 LLM Only
      </span>
    )
  }

  const score = relevanceScore ?? 0
  const scoreLabel =
    relevanceScore != null ? `RAG · ${(score * 100).toFixed(0)}%` : "RAG"

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        data-testid="rag-badge"
        onClick={() => hasChunks && setExpanded((v) => !v)}
        className={cn(
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium transition-colors",
          "text-muted-foreground bg-muted/60",
          hasChunks && "cursor-pointer hover:opacity-80",
        )}
        aria-expanded={hasChunks ? expanded : undefined}
        title={
          relevanceScore != null
            ? `Retrieval score: ${score.toFixed(4)}`
            : undefined
        }
      >
        📚 {scoreLabel}
        {hasChunks && (
          <span className="opacity-60">{expanded ? "▲" : "▼"}</span>
        )}
      </button>

      {/* Expandable chunk ID list */}
      {expanded && hasChunks && (
        <div className="ml-1 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <p className="mb-1 font-medium text-foreground">
            Retrieved chunks ({chunkIds.length})
          </p>
          <ul className="space-y-0.5 font-mono">
            {chunkIds.map((id) => (
              <li key={id} className="truncate">
                {id}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ── table cell styling ────────────────────────────────────────────────────────

function nodeText(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node)
  if (Array.isArray(node)) return node.map(nodeText).join("")
  return ""
}

// $1,234.56 / +5.2% / -3.1 — an explicit sign or unit, not a bare year
const NUMERIC_CELL = /^[+-]?[$€£]?[\d,]+(\.\d+)?%?$/

function TableCell({ children }: { children?: React.ReactNode }) {
  const text = nodeText(children).trim()
  const isNumeric = NUMERIC_CELL.test(text)
  // Colorize only explicitly signed values — the sign itself remains the
  // non-color channel (WCAG 1.4.1), and plain years/counts stay neutral.
  const signColor = isNumeric
    ? text.startsWith("+")
      ? "text-green-600 dark:text-green-500"
      : text.startsWith("-")
        ? "text-red-600 dark:text-red-500"
        : ""
    : ""
  return (
    <td
      className={cn(
        "border border-border px-2 py-1",
        isNumeric && "text-right font-mono",
        signColor,
      )}
    >
      {children}
    </td>
  )
}

// ── MessageBubble ─────────────────────────────────────────────────────────────

interface Props {
  message: MessageRead
  agentStatus?: string | null
  toolCall?: ToolCall | null
  sources?: Source[]
  isStreaming?: boolean
  streamingChartData?: ChartData | null
}

function MessageBubble({
  message,
  agentStatus,
  toolCall,
  sources,
  isStreaming,
  streamingChartData,
}: Props) {
  const isUser = message.role === "user"
  const isEmpty = message.content === ""
  const hasSources = sources != null && sources.length > 0

  // Show RAG badge on settled assistant messages that have the field set
  const showRagBadge =
    !isUser && !isStreaming && message.rag_used !== undefined

  if (isUser) {
    return (
      <div data-testid="message-user" className="flex justify-end">
        <div className="max-w-[85%] rounded-3xl bg-muted px-4 py-3 text-sm leading-relaxed text-foreground">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div data-testid="message-assistant" className="flex flex-col gap-2">
      <div
        className={cn(
          "border-l-2 border-primary/30 pl-4 pt-1 text-sm leading-relaxed text-foreground",
          isEmpty && "text-muted-foreground",
        )}
      >
        {isEmpty
          ? "…"
          : (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children }) => (
                    <a href={href} target="_blank" rel="noopener noreferrer"
                      className="text-primary underline underline-offset-2 hover:opacity-80">
                      {children}
                    </a>
                  ),
                  p: ({ children }) => (
                    <p className="mb-2 last:mb-0">{children}</p>
                  ),
                  ul: ({ children }) => (
                    <ul className="mb-2 ml-4 list-disc space-y-1">{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="mb-2 ml-4 list-decimal space-y-1">{children}</ol>
                  ),
                  li: ({ children }) => (
                    <li className="leading-relaxed">{children}</li>
                  ),
                  h1: ({ children }) => (
                    <h1 className="mb-2 text-base font-bold">{children}</h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="mb-2 text-sm font-bold">{children}</h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="mb-1 text-sm font-semibold">{children}</h3>
                  ),
                  code: ({ inline, children, ...props }: { inline?: boolean; children?: React.ReactNode; className?: string }) =>
                    inline ? (
                      <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs"
                        {...props}>{children}</code>
                    ) : (
                      <pre className="mb-2 overflow-x-auto rounded-lg bg-muted p-3">
                        <code className="font-mono text-xs">{children}</code>
                      </pre>
                    ),
                  blockquote: ({ children }) => (
                    <blockquote className="mb-2 border-l-2 border-border pl-3
                      text-muted-foreground italic">{children}</blockquote>
                  ),
                  table: ({ children }) => (
                    <div className="mb-2 overflow-x-auto rounded-lg border border-border">
                      <table className="w-full text-sm border-collapse">{children}</table>
                    </div>
                  ),
                  th: ({ children }) => (
                    <th className="border border-border px-2 py-1 text-left
                      font-semibold bg-muted">{children}</th>
                  ),
                  td: ({ children }) => <TableCell>{children}</TableCell>,
                  strong: ({ children }) => (
                    <strong className="font-semibold">{children}</strong>
                  ),
                  hr: () => <hr className="my-3 border-border" />,
                }}
              >
                {message.content}
              </ReactMarkdown>
            )}
      </div>

      {agentStatus && isEmpty && <AgentStatus node={agentStatus} toolCall={toolCall ?? undefined} />}
      {hasSources && <SourceList sources={sources} />}

      {showRagBadge && (
        <RagBadge
          ragUsed={Boolean(message.rag_used)}
          relevanceScore={message.relevance_score}
          chunkIds={message.retrieved_chunk_ids}
        />
      )}

      {/* Settled message chart — from DB on reload */}
      {!isUser && !isStreaming && message.chart_data && (
        <ChartBlock data={message.chart_data as ChartData} />
      )}
      {/* Streaming chart — arrives via chart_data SSE event before done */}
      {!isUser && isStreaming && streamingChartData && (
        <ChartBlock data={streamingChartData} />
      )}
    </div>
  )
}

// Memoized: settled bubbles receive referentially stable props, so per-token
// re-renders of the page no longer re-parse every message's markdown. The
// streaming bubble gets a fresh message object each render and updates as usual.
export default memo(MessageBubble)
