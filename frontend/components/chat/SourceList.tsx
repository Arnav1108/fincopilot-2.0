import type { Source } from "@/lib/types"

interface Props {
  sources: Source[]
}

/**
 * Renders the documents behind a RAG answer. The `sources` SSE event carries
 * retrieved chunks (content + metadata), not URLs — so we show one entry per
 * distinct source document, with its chunk count.
 */
export default function SourceList({ sources }: Props) {
  const byDocument = new Map<string, { label: string; chunkCount: number }>()
  for (const chunk of sources) {
    const key =
      chunk.metadata?.document_id ??
      chunk.metadata?.document_filename ??
      "unknown"
    const existing = byDocument.get(key)
    if (existing) {
      existing.chunkCount += 1
    } else {
      byDocument.set(key, {
        label: chunk.metadata?.document_filename ?? "Unknown document",
        chunkCount: 1,
      })
    }
  }

  if (byDocument.size === 0) return null

  return (
    <div className="mt-2 space-y-0.5" data-testid="source-list">
      <p className="text-xs font-medium text-muted-foreground">Sources</p>
      {Array.from(byDocument.entries()).map(([key, doc]) => (
        <p key={key} className="flex items-center gap-1.5 text-xs text-muted-foreground truncate">
          <span aria-hidden>📄</span>
          <span className="truncate">{doc.label}</span>
          <span className="shrink-0 opacity-60">
            · {doc.chunkCount} chunk{doc.chunkCount !== 1 ? "s" : ""}
          </span>
        </p>
      ))}
    </div>
  )
}
