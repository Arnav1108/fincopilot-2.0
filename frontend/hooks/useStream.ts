"use client"

import { useCallback, useState } from "react"
import type { IngestProgress, Source, ToolCall } from "@/lib/types"

const BASE = process.env.NEXT_PUBLIC_API_URL

interface UseStreamOptions {
  onToken: (token: string) => void
  onNodeUpdate: (node: string) => void
  onSources: (sources: Source[]) => void
  onDone: (messageId: string | null) => void
  onError: (err: Error) => void
  /** Called for each ingest_progress SSE event while files are ingesting. */
  onIngestProgress?: (progress: IngestProgress) => void
  /** Called once when all files have been ingested successfully. */
  onIngestComplete?: (documentCount: number) => void
  /** Called for each tool_call SSE event emitted by the agent executor. */
  onToolCall?: (toolCall: ToolCall) => void
}

interface UseStreamResult {
  startStream: (
    conversationId: string,
    message: string,
    token: string,
    files?: File[],
  ) => Promise<void>
  isStreaming: boolean
}

export default function useStream(options: UseStreamOptions): UseStreamResult {
  const {
    onToken,
    onNodeUpdate,
    onSources,
    onDone,
    onError,
    onIngestProgress,
    onIngestComplete,
    onToolCall,
  } = options

  const [isStreaming, setIsStreaming] = useState(false)

  const startStream = useCallback(
    async (
      conversationId: string,
      message: string,
      token: string,
      files?: File[],
    ) => {
      setIsStreaming(true)
      try {
        // Always multipart/form-data — backend uses FastAPI Form() params.
        // Do NOT set Content-Type manually; the browser supplies the boundary.
        const body = new FormData()
        body.append("message", message)
        body.append("model", "gpt-4o")
        if (files?.length) {
          for (const file of files) {
            body.append("files", file, file.name)
          }
        }

        const res = await fetch(
          `${BASE}/api/v1/conversations/${conversationId}/stream`,
          {
            method: "POST",
            headers: { Authorization: `Bearer ${token}` },
            body,
          },
        )

        if (!res.ok) {
          const text = await res.text().catch(() => "")
          onError(new Error(_friendlyHttpError(res.status, text)))
          return
        }

        const reader = res.body!.getReader()
        const decoder = new TextDecoder()
        let buffer = ""

        outer: while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const blocks = buffer.split("\n\n")
          buffer = blocks.pop() ?? ""

          for (const block of blocks) {
            if (!block.trim()) continue
            const lines = block.split("\n")
            const eventLine = lines.find((l) => l.startsWith("event:"))
            const dataLine = lines.find((l) => l.startsWith("data:"))
            if (!eventLine || !dataLine) continue

            const event = eventLine.slice("event:".length).trim()
            let data: Record<string, unknown>
            try {
              data = JSON.parse(dataLine.slice("data:".length).trim())
            } catch {
              continue
            }

            switch (event) {
              case "node_update":
                onNodeUpdate(data.node as string)
                break
              case "token":
                onToken(data.token as string)
                break
              case "sources":
                onSources((data.sources as Source[]) ?? [])
                break
              case "done":
                onDone((data.message_id as string | null) ?? null)
                break
              case "ingest_progress":
                onIngestProgress?.(data as unknown as IngestProgress)
                break
              case "ingest_complete":
                onIngestComplete?.((data.document_count as number) ?? 0)
                break
              case "tool_call":
                onToolCall?.({
                  tool_name: data.tool_name as string,
                  status: data.status as ToolCall["status"],
                  message: data.message as string | undefined,
                })
                break
              case "error":
                onError(
                  new Error(
                    _friendlySseError(
                      data.code as string | undefined,
                      data.message as string | undefined,
                      data.failed_files as string[] | undefined,
                    ),
                  ),
                )
                break outer
            }
          }
        }
      } catch (err) {
        onError(err instanceof Error ? err : new Error(String(err)))
      } finally {
        setIsStreaming(false)
      }
    },
    [onToken, onNodeUpdate, onSources, onDone, onError, onIngestProgress, onIngestComplete, onToolCall],
  )

  return { startStream, isStreaming }
}

// ── error message helpers ─────────────────────────────────────────────────────

function _friendlyHttpError(status: number, body: string): string {
  let detail: string | undefined
  try {
    detail = JSON.parse(body)?.detail
  } catch {}

  if (status === 404) return "Conversation not found."
  if (status === 408) return "⏱️ Documents took too long to process. Try again."
  if (status === 422) {
    if (detail?.includes("file type") || detail?.includes("Unsupported"))
      return "Only PDF, DOCX, CSV, TXT, and HTML files are supported."
    if (detail?.includes("100 MB") || detail?.includes("exceeds"))
      return "File too large — maximum 100 MB per file."
    if (detail?.includes("blank")) return "Message must not be blank."
    return detail ?? "Invalid request (422)."
  }
  return detail ?? `Request failed (${status}).`
}

function _friendlySseError(
  code?: string,
  message?: string,
  failedFiles?: string[],
): string {
  if (code === "ingest_timeout")
    return "⏱️ Documents took too long to process. They're still ingesting — try your question again shortly."
  if (code === "ingest_failed") {
    const files = failedFiles?.join(", ")
    return files
      ? `❌ Ingestion failed for: ${files}`
      : "❌ One or more files failed to ingest."
  }
  return message ?? "An unexpected error occurred."
}
