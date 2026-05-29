"use client"

import { useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { cn } from "@/lib/utils"
import { getConversationDocuments } from "@/lib/api"
import type { DocumentRead } from "@/lib/types"

interface Props {
  conversationId: string
  isOpen: boolean
  onToggle: () => void
}

const STATUS_CLASSES: Record<DocumentRead["status"], string> = {
  pending: "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30",
  processing: "bg-blue-500/20 text-blue-400 border border-blue-500/30 animate-pulse",
  ready: "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30",
  failed: "bg-red-500/20 text-red-400 border border-red-500/30",
}

export default function DocumentPanel({ conversationId, isOpen, onToggle }: Props) {
  const { getToken } = useAuth()
  const [documents, setDocuments] = useState<DocumentRead[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false

    async function load() {
      const token = await getToken()
      if (!token || cancelled) return
      setLoading(true)
      try {
        const docs = await getConversationDocuments(token, conversationId)
        if (!cancelled) setDocuments(docs)
      } catch {
        // Panel is non-critical — swallow errors silently
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [conversationId, getToken])

  if (!isOpen) {
    return (
      <div className="flex w-8 flex-shrink-0 flex-col items-center border-l border-border bg-card">
        <button
          type="button"
          onClick={onToggle}
          className="mt-3 flex flex-col items-center gap-1 text-muted-foreground transition-colors hover:text-foreground"
          aria-label="Open documents panel"
        >
          <ChevronLeft size={14} />
          <span className="mt-1 select-none text-xs [writing-mode:vertical-rl] rotate-180">
            Documents
          </span>
        </button>
      </div>
    )
  }

  return (
    <div className="flex w-72 flex-shrink-0 flex-col border-l border-border bg-card">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-3">
        <span className="text-sm font-medium text-foreground">Documents</span>
        <button
          type="button"
          onClick={onToggle}
          className="text-muted-foreground transition-colors hover:text-foreground"
          aria-label="Close documents panel"
        >
          <ChevronRight size={14} />
        </button>
      </div>

      {/* Document list */}
      <div className="flex-1 overflow-y-auto space-y-1 px-2 py-2">
        {loading && (
          <p className="px-2 py-2 text-xs text-muted-foreground">Loading…</p>
        )}

        {!loading && documents.length === 0 && (
          <p className="px-2 py-4 text-center text-xs text-muted-foreground">
            No documents yet.
          </p>
        )}

        {documents.map((doc) => (
          <div
            key={doc.id}
            className="rounded-lg px-2 py-2 transition-colors hover:bg-muted/50"
          >
            {/* Filename + ticker */}
            <div className="flex min-w-0 items-center gap-1.5">
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                {doc.filename}
              </span>
              {doc.ticker && (
                <span className="flex-shrink-0 rounded bg-muted px-1 py-0.5 text-xs text-muted-foreground">
                  {doc.ticker}
                </span>
              )}
            </div>

            {/* Status chip + doc_type + chunk count */}
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <span
                className={cn(
                  "rounded px-1.5 py-0.5 text-xs font-medium",
                  STATUS_CLASSES[doc.status],
                )}
              >
                {doc.status}
              </span>
              <span className="text-xs text-muted-foreground">{doc.doc_type}</span>
              {doc.status === "ready" && doc.chunk_count !== null && (
                <span className="text-xs text-muted-foreground">
                  {doc.chunk_count} chunks
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
