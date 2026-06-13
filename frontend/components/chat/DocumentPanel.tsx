"use client"

import { useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { PanelRight } from "lucide-react"
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
      <div className="flex w-[52px] flex-shrink-0 flex-col items-center border-l border-border bg-card transition-all duration-200">
        <div className="flex flex-col items-center gap-1 px-1.5 py-3">
          <button
            type="button"
            onClick={onToggle}
            title="Expand documents panel"
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            aria-label="Open documents panel"
          >
            <PanelRight size={16} />
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex w-72 flex-shrink-0 flex-col border-l border-border bg-card">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-3 mb-2">
        <button
          type="button"
          onClick={onToggle}
          title="Collapse documents panel"
          className="rounded-md p-1.5 hover:bg-muted text-muted-foreground transition-colors hover:text-foreground"
          aria-label="Close documents panel"
        >
          <PanelRight size={16} />
        </button>
        <span className="text-sm font-medium text-foreground">Documents</span>
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
                <span className="flex-shrink-0 max-w-[60px] truncate rounded bg-muted px-1 py-0.5 text-xs text-muted-foreground">
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
