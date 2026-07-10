"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { ArrowDown } from "lucide-react"
import MessageBubble from "./MessageBubble"
import ExamplePrompts from "./ExamplePrompts"
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
  /** Invoked when the user clicks an example prompt in the empty state. */
  onSuggestion?: (prompt: string) => void
}

export default function MessageList({
  messages,
  streamingContent,
  agentStatus,
  toolCall,
  streamingSources,
  isStreaming,
  streamingChartData,
  onSuggestion,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const shouldAutoScrollRef = useRef(true)
  const [showJumpToLatest, setShowJumpToLatest] = useState(false)

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
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50
    shouldAutoScrollRef.current = atBottom
    setShowJumpToLatest(!atBottom)
  }

  const scrollToBottom = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    shouldAutoScrollRef.current = true
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
    setShowJumpToLatest(false)
  }, [])

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
    <div className="relative flex-1 min-h-0">
      <div
        ref={containerRef}
        data-testid="message-list"
        onScroll={handleScroll}
        className="h-full overflow-y-auto scrollbar-thin"
      >
        {/* Live region: announces agent progress and completion to screen readers */}
        <div aria-live="polite" role="status" className="sr-only">
          {isStreaming ? agentStatus ?? "Assistant is responding" : announcement}
        </div>

        {isEmpty ? (
          <div className="flex h-full flex-col items-center justify-center gap-6 px-4">
            <div className="flex flex-col items-center gap-2 text-center">
              <h2 className="text-lg font-semibold tracking-tight text-foreground">
                What can I help you research?
              </h2>
              <p className="max-w-sm text-sm text-muted-foreground">
                Ask about a company, filing, or market event — or start from
                one of these.
              </p>
            </div>
            {onSuggestion && <ExamplePrompts onSelect={onSuggestion} />}
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

      {/* Jump-to-latest pill — appears whenever the user has scrolled away
          from the bottom, so streamed tokens never accumulate invisibly */}
      {showJumpToLatest && !isEmpty && (
        <button
          type="button"
          onClick={scrollToBottom}
          aria-label="Jump to latest message"
          className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground shadow-lg transition-colors hover:border-primary/40 hover:text-primary animate-in fade-in slide-in-from-bottom-2"
        >
          <ArrowDown size={13} aria-hidden />
          Jump to latest
        </button>
      )}
    </div>
  )
}
