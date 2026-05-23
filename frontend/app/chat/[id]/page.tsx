"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useAuth } from "@clerk/nextjs"
import useMessages from "@/hooks/useMessages"
import useStream from "@/hooks/useStream"
import { useConversations } from "@/hooks/useConversations"
import MessageList from "@/components/chat/MessageList"
import InputBar from "@/components/chat/InputBar"
import type { Source } from "@/lib/types"

export default function ConversationPage({ params }: { params: { id: string } }) {
  const id = params.id
  const { getToken } = useAuth()
  const { messages, isLoading, notFound, load, addMessage } = useMessages()
  const { bumpToTop } = useConversations()

  const [streamingContent, setStreamingContent] = useState("")
  const [agentStatus, setAgentStatus] = useState<string | null>(null)
  const [streamingSources, setStreamingSources] = useState<Source[]>([])

  // Ref tracks accumulated content so onDone never has a stale closure
  const streamingContentRef = useRef("")

  const { startStream, isStreaming } = useStream({
    onToken: useCallback((token: string) => {
      streamingContentRef.current += token
      setStreamingContent(streamingContentRef.current)
    }, []),

    onNodeUpdate: useCallback((node: string) => {
      setAgentStatus(node)
    }, []),

    onSources: useCallback((sources: Source[]) => {
      setStreamingSources(sources)
    }, []),

    onDone: useCallback(
      async (_messageId: string | null) => {
        streamingContentRef.current = ""
        setStreamingContent("")
        setAgentStatus(null)
        bumpToTop(id)
        await load(id)
      },
      [bumpToTop, id, load],
    ),

    onError: useCallback((err: Error) => {
      console.error("[useStream] error", err)
      setAgentStatus(null)
      streamingContentRef.current = ""
      setStreamingContent("")
    }, []),
  })

  useEffect(() => {
    load(id)
  }, [id, load])

  const handleSend = useCallback(
    async (message: string) => {
      const token = await getToken()
      if (!token) return
      addMessage({
        id: crypto.randomUUID(),
        conversation_id: id,
        role: "user",
        content: message,
        created_at: new Date().toISOString(),
      })
      streamingContentRef.current = ""
      setStreamingContent("")
      setAgentStatus(null)
      setStreamingSources([])
      await startStream(id, message, token)
    },
    [getToken, addMessage, id, startStream],
  )

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
      <MessageList
        messages={messages}
        streamingContent={streamingContent}
        agentStatus={agentStatus}
        streamingSources={streamingSources}
        isStreaming={isStreaming}
      />
      <InputBar onSend={handleSend} disabled={isStreaming} />
    </div>
  )
}
