"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { API_BASE, DEFAULT_MODEL } from "@/lib/api"
import type { ChartData, ConfirmationRequired, IngestProgress, Source, ToolCall } from "@/lib/types"

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
  /** Called when the backend needs user confirmation before downloading a filing. */
  onConfirmationRequired?: (req: ConfirmationRequired) => void
  /** Called when the backend acknowledges the user's yes/no confirmation. */
  onConfirmed?: (token: string, answer: string) => void
  /** Called when a chart_data SSE event is received for the current response. */
  onChartData?: (chartData: ChartData) => void
}

interface UseStreamResult {
  startStream: (
    conversationId: string,
    message: string,
    token: string,
    files?: File[],
  ) => Promise<void>
  isStreaming: boolean
  stop: () => void
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
    onConfirmationRequired,
    onConfirmed,
    onChartData,
  } = options

  const [isStreaming, setIsStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setIsStreaming(false)
  }, [])

  // Abort any in-flight stream when the owning component unmounts so it
  // doesn't keep running (and firing stale callbacks) in the background.
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
      abortRef.current = null
    }
  }, [])

  const startStream = useCallback(
    async (
      conversationId: string,
      message: string,
      token: string,
      files?: File[],
    ) => {
      setIsStreaming(true)
      const controller = new AbortController()
      abortRef.current = controller
      // The stream must terminate with either a `done` or an `error` event.
      // EOF without one means the connection dropped mid-response.
      let settled = false
      try {
        // Always multipart/form-data — backend uses FastAPI Form() params.
        // Do NOT set Content-Type manually; the browser supplies the boundary.
        const body = new FormData()
        body.append("message", message)
        body.append("model", DEFAULT_MODEL)
        if (files?.length) {
          for (const file of files) {
            body.append("files", file, file.name)
          }
        }

        const res = await fetch(
          `${API_BASE}/api/v1/conversations/${conversationId}/stream`,
          {
            method: "POST",
            headers: { Authorization: `Bearer ${token}` },
            body,
            signal: controller.signal,
          },
        )

        if (!res.ok) {
          const text = await res.text().catch(() => "")
          settled = true
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
            if (data === null || typeof data !== "object") continue

            switch (event) {
              case "node_update":
                if (typeof data.node === "string") onNodeUpdate(data.node)
                break
              case "token":
                if (typeof data.token === "string") onToken(data.token)
                break
              case "sources":
                if (Array.isArray(data.chunks)) onSources(data.chunks as Source[])
                break
              case "done":
                settled = true
                onDone(typeof data.message_id === "string" ? data.message_id : null)
                break
              case "ingest_progress":
                if (typeof data.total === "number") {
                  onIngestProgress?.(data as unknown as IngestProgress)
                }
                break
              case "ingest_complete":
                onIngestComplete?.(
                  typeof data.document_count === "number" ? data.document_count : 0,
                )
                break
              case "tool_call":
                if (typeof data.tool_name === "string" && typeof data.status === "string") {
                  onToolCall?.({
                    tool_name: data.tool_name,
                    status: data.status as ToolCall["status"],
                    message: typeof data.message === "string" ? data.message : undefined,
                  })
                }
                break
              case "confirmation_required":
                if (typeof data.token === "string") {
                  onConfirmationRequired?.(data as unknown as ConfirmationRequired)
                }
                break
              case "confirmed":
                if (typeof data.token === "string" && typeof data.answer === "string") {
                  onConfirmed?.(data.token, data.answer)
                }
                break
              case "chart_data":
                if (typeof data.chart_type === "string" && Array.isArray(data.series)) {
                  onChartData?.(data as unknown as ChartData)
                }
                break
              case "error":
                settled = true
                onError(
                  new Error(
                    _friendlySseError(
                      typeof data.code === "string" ? data.code : undefined,
                      typeof data.message === "string" ? data.message : undefined,
                      Array.isArray(data.failed_files)
                        ? (data.failed_files as string[])
                        : undefined,
                    ),
                  ),
                )
                // Release the connection — we're done consuming this stream.
                await reader.cancel().catch(() => {})
                break outer
            }
          }
        }

        // EOF without a terminal event: proxy timeout / backend crash mid-stream.
        if (!settled) {
          onError(
            new Error(
              "Connection lost before the response finished. The partial answer may have been saved — it will appear after reloading.",
            ),
          )
        }
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return
        onError(err instanceof Error ? err : new Error(String(err)))
      } finally {
        if (abortRef.current === controller) abortRef.current = null
        setIsStreaming(false)
      }
    },
    [onToken, onNodeUpdate, onSources, onDone, onError, onIngestProgress, onIngestComplete, onToolCall, onConfirmationRequired, onConfirmed, onChartData],
  )

  return { startStream, isStreaming, stop }
}

// ── error message helpers ─────────────────────────────────────────────────────

function _friendlyHttpError(status: number, body: string): string {
  let detail: string | undefined
  try {
    // FastAPI 422s often carry an array of error objects — only surface strings.
    const raw: unknown = JSON.parse(body)?.detail
    detail = typeof raw === "string" ? raw : undefined
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
