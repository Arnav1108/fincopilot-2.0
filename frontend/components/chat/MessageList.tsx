"use client"

import { useEffect, useRef } from "react"
import MessageBubble from "./MessageBubble"
import type { MessageRead, Source } from "@/lib/types"

const STREAMING_ID = "__streaming__"

interface Props {
  messages: MessageRead[]
  streamingContent: string
  agentStatus: string | null
  streamingSources: Source[]
  isStreaming: boolean
}

export default function MessageList({
  messages,
  streamingContent,
  agentStatus,
  streamingSources,
  isStreaming,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const shouldAutoScrollRef = useRef(true)

  const handleScroll = () => {
    const el = containerRef.current
    if (!el) return
    shouldAutoScrollRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 50
  }

  useEffect(() => {
    if (!shouldAutoScrollRef.current) return
    const el = containerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, streamingContent])

  const isEmpty = messages.length === 0 && !isStreaming

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto"
    >
      {isEmpty ? (
        <div className="h-full flex items-center justify-center px-4">
          <p className="text-muted-foreground text-sm text-center">
            Ask me anything about a company, filing, or market event.
          </p>
        </div>
      ) : (
        <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}

          {isStreaming && (
            <MessageBubble
              key={STREAMING_ID}
              message={{
                id: STREAMING_ID,
                conversation_id: "",
                role: "assistant",
                content: streamingContent,
                created_at: new Date().toISOString(),
              }}
              agentStatus={agentStatus}
              sources={streamingSources}
              isStreaming={true}
            />
          )}
        </div>
      )}
    </div>
  )
}
