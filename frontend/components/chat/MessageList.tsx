"use client"

import { useEffect, useRef, useState } from "react"
import MessageBubble from "./MessageBubble"
import type { ChartData, MessageRead, Source, ToolCall } from "@/lib/types"

const STREAMING_ID = "__streaming__"

interface Props {
  messages: MessageRead[]
  streamingContent: string
  agentStatus: string | null
  toolCall: ToolCall | null
  streamingSources: Source[]
  isStreaming: boolean
  streamingChartData?: ChartData | null
}

export default function MessageList({
  messages,
  streamingContent,
  agentStatus,
  toolCall,
  streamingSources,
  isStreaming,
  streamingChartData,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const shouldAutoScrollRef = useRef(true)

  // Screen-reader announcement: "Response complete" once a stream finishes
  const wasStreamingRef = useRef(false)
  const [announcement, setAnnouncement] = useState("")
  useEffect(() => {
    if (isStreaming) {
      wasStreamingRef.current = true
      setAnnouncement("")
    } else if (wasStreamingRef.current) {
      wasStreamingRef.current = false
      setAnnouncement("Response complete")
    }
  }, [isStreaming])

  const handleScroll = () => {
    const el = containerRef.current
    if (!el) return
    shouldAutoScrollRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 50
  }

  useEffect(() => {
    if (!shouldAutoScrollRef.current) return
    const el = containerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, streamingContent, streamingChartData, agentStatus, toolCall])

  // Keep the bubble rendered while content exists even after the stream ends,
  // so the finished answer doesn't blink out before the reloaded messages land.
  const showStreamingBubble = isStreaming || streamingContent.length > 0
  const isEmpty = messages.length === 0 && !showStreamingBubble

  return (
    <div
      ref={containerRef}
      data-testid="message-list"
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar-track]:transparent"
    >
      {/* Live region: announces agent progress and completion to screen readers */}
      <div aria-live="polite" role="status" className="sr-only">
        {isStreaming ? agentStatus ?? "Assistant is responding" : announcement}
      </div>

      {isEmpty ? (
        <div className="h-full flex items-center justify-center px-4">
          <p className="text-muted-foreground text-sm text-center">
            Ask me anything about a company, filing, or market event.
          </p>
        </div>
      ) : (
        <div className="max-w-3xl mx-auto px-4 pt-6 pb-32 space-y-6">
          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}

          {showStreamingBubble && (
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
              toolCall={toolCall}
              sources={streamingSources}
              isStreaming={true}
              streamingChartData={streamingChartData}
            />
          )}
        </div>
      )}
    </div>
  )
}
