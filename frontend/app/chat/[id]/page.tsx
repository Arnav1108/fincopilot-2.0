"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import useMessages from "@/hooks/useMessages"
import type { Source } from "@/lib/types"

export default function ConversationPage({ params }: { params: { id: string } }) {
  const { messages, isLoading, notFound, load, addMessage } = useMessages()
  const [streamingContent, setStreamingContent] = useState("")
  const [agentStatus, setAgentStatus] = useState<string | null>(null)
  const [streamingSources, setStreamingSources] = useState<Source[]>([])
  const [isStreaming, setIsStreaming] = useState(false)

  useEffect(() => {
    load(params.id)
  }, [params.id, load])

  if (notFound) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3">
        <p className="text-foreground text-sm">Conversation not found.</p>
        <Link href="/chat" className="text-sm text-muted-foreground underline">
          Back to chat
        </Link>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <span className="text-muted-foreground text-sm">Loading…</span>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* MessageList — wired in Step 14 */}
      <div className="flex-1 overflow-y-auto p-4 text-muted-foreground text-sm">
        {messages.length} message{messages.length !== 1 ? "s" : ""} loaded
      </div>

      {/* InputBar — wired in Step 15 */}
      <div className="border-t border-border px-4 py-3 text-muted-foreground text-sm">
        Input goes here
      </div>
    </div>
  )
}
