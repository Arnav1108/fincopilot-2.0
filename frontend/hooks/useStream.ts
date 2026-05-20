"use client"

import { useCallback, useState } from "react"
import type { Source } from "@/lib/types"

const BASE = process.env.NEXT_PUBLIC_API_URL

interface UseStreamOptions {
  onToken: (token: string) => void
  onNodeUpdate: (node: string) => void
  onSources: (sources: Source[]) => void
  onDone: (messageId: string | null) => void
  onError: (err: Error) => void
}

interface UseStreamResult {
  startStream: (conversationId: string, message: string, token: string) => Promise<void>
  isStreaming: boolean
}

export default function useStream(options: UseStreamOptions): UseStreamResult {
  const { onToken, onNodeUpdate, onSources, onDone, onError } = options
  const [isStreaming, setIsStreaming] = useState(false)

  const startStream = useCallback(
    async (conversationId: string, message: string, token: string) => {
      setIsStreaming(true)
      try {
        const res = await fetch(
          `${BASE}/api/v1/conversations/${conversationId}/stream`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({ conversation_id: conversationId, message }),
          },
        )

        if (res.status !== 200) {
          const body = await res.text().catch(() => "")
          onError(new Error(`Stream request failed (${res.status}): ${body}`))
          return
        }

        const reader = res.body!.getReader()
        const decoder = new TextDecoder()
        let buffer = ""

        while (true) {
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
            const data = JSON.parse(dataLine.slice("data:".length).trim())

            switch (event) {
              case "node_update":
                onNodeUpdate(data.node)
                break
              case "token":
                onToken(data.token)
                break
              case "sources":
                onSources(data.sources)
                break
              case "done":
                onDone(data.message_id)
                break
              case "tool_call":
                console.debug("[useStream] tool_call", data.tool, data.input)
                break
            }
          }
        }
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") {
          onError(err)
        } else {
          onError(err instanceof Error ? err : new Error(String(err)))
        }
      } finally {
        setIsStreaming(false)
      }
    },
    [onToken, onNodeUpdate, onSources, onDone, onError],
  )

  return { startStream, isStreaming }
}
