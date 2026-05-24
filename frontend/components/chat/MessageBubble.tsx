"use client"

import { useState } from "react"
import { cn } from "@/lib/utils"
import type { MessageRead, Source } from "@/lib/types"
import AgentStatus from "./AgentStatus"
import SourceList from "./SourceList"
import ComparisonTable, { hasMarkdownTable } from "./ComparisonTable"

// ── RAG badge ─────────────────────────────────────────────────────────────────

interface RagBadgeProps {
  ragUsed: boolean
  relevanceScore?: number | null
  chunkIds?: string[] | null
}

function ragScoreColor(score: number): string {
  if (score >= 0.75) return "text-emerald-700 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-950/40"
  if (score >= 0.50) return "text-amber-700 bg-amber-50 dark:text-amber-400 dark:bg-amber-950/40"
  return "text-red-700 bg-red-50 dark:text-red-400 dark:bg-red-950/40"
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
        onClick={() => hasChunks && setExpanded((v) => !v)}
        className={cn(
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium transition-colors",
          ragScoreColor(score),
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

// ── MessageBubble ─────────────────────────────────────────────────────────────

interface Props {
  message: MessageRead
  agentStatus?: string | null
  sources?: Source[]
  isStreaming?: boolean
}

export default function MessageBubble({
  message,
  agentStatus,
  sources,
  isStreaming,
}: Props) {
  const isUser = message.role === "user"
  const isEmpty = message.content === ""
  const hasSources = !isStreaming && sources && sources.length > 0
  const showTable =
    !isUser && !isStreaming && !isEmpty && hasMarkdownTable(message.content)

  // Show RAG badge on settled assistant messages that have the field set
  const showRagBadge =
    !isUser && !isStreaming && message.rag_used !== undefined

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-3xl bg-muted px-4 py-3 text-sm leading-relaxed text-foreground">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div
        className={cn(
          "text-sm leading-relaxed text-foreground",
          isEmpty && "text-muted-foreground",
        )}
      >
        {isEmpty
          ? "…"
          : showTable
            ? <ComparisonTable content={message.content} />
            : message.content}
      </div>

      {agentStatus && <AgentStatus node={agentStatus} />}
      {hasSources && <SourceList sources={sources!} />}

      {showRagBadge && (
        <RagBadge
          ragUsed={Boolean(message.rag_used)}
          relevanceScore={message.relevance_score}
          chunkIds={message.retrieved_chunk_ids}
        />
      )}
    </div>
  )
}
