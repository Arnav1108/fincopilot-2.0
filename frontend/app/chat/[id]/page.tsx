"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useAuth } from "@clerk/nextjs"
import useMessages from "@/hooks/useMessages"
import useStream from "@/hooks/useStream"
import { useConversations } from "@/hooks/useConversations"
import MessageList from "@/components/chat/MessageList"
import InputBar from "@/components/chat/InputBar"
import DocumentIngestionBanner from "@/components/chat/DocumentIngestionBanner"
import ConfirmationBanner from "@/components/chat/ConfirmationBanner"
import DocumentPanel from "@/components/chat/DocumentPanel"
import type { ConfirmationRequired, IngestProgress, Source, ToolCall } from "@/lib/types"

const BASE = process.env.NEXT_PUBLIC_API_URL

export default function ConversationPage({ params }: { params: { id: string } }) {
  const id = params.id
  const { getToken } = useAuth()
  const { messages, isLoading, notFound, load, addMessage } = useMessages()
  const { bumpToTop, refresh } = useConversations()

  const [streamingContent, setStreamingContent] = useState("")
  const [agentStatus, setAgentStatus] = useState<string | null>(null)
  const [toolCall, setToolCall] = useState<ToolCall | null>(null)
  const [streamingSources, setStreamingSources] = useState<Source[]>([])
  const [ingestProgress, setIngestProgress] = useState<IngestProgress | null>(null)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [isPanelOpen, setIsPanelOpen] = useState(true)
  const [confirmationRequest, setConfirmationRequest] = useState<ConfirmationRequired | null>(null)

  // Ref tracks accumulated content so onDone never has a stale closure
  const streamingContentRef = useRef("")

  const { startStream, isStreaming } = useStream({
    onToken: useCallback((token: string) => {
      streamingContentRef.current += token
      setStreamingContent(streamingContentRef.current)
    }, []),

    onNodeUpdate: useCallback((node: string) => {
      // Clear ingestion state once the agent takes over
      setIngestProgress(null)
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
        setToolCall(null)
        setIngestProgress(null)
        setConfirmationRequest(null)
        bumpToTop(id)
        await load(id)
        await refresh()
      },
      [bumpToTop, id, load, refresh],
    ),

    onError: useCallback((err: Error) => {
      console.error("[useStream] error", err)
      setStreamError(err.message)
      setAgentStatus(null)
      setToolCall(null)
      setIngestProgress(null)
      setConfirmationRequest(null)
      streamingContentRef.current = ""
      setStreamingContent("")
    }, []),

    onIngestProgress: useCallback((progress: IngestProgress) => {
      setIngestProgress(progress)
    }, []),

    onIngestComplete: useCallback((_count: number) => {
      // Ingestion done; Phase 2 (agent) begins — clear progress indicator
      setIngestProgress(null)
    }, []),

    onToolCall: useCallback((tc: ToolCall) => {
      setToolCall(tc)
    }, []),

    onConfirmationRequired: useCallback((req: ConfirmationRequired) => {
      setConfirmationRequest(req)
    }, []),

    onConfirmed: useCallback((_token: string, _answer: string) => {
      setConfirmationRequest(null)
    }, []),
  })

  const sendConfirmation = useCallback(
    async (answer: "yes" | "no", token: string) => {
      setConfirmationRequest(null)
      const authToken = await getToken()
      if (!authToken) return
      const body = new FormData()
      body.append("message", `CONFIRM:${answer}:${token}`)
      body.append("model", "gpt-4o")
      fetch(`${BASE}/api/v1/conversations/${id}/stream`, {
        method: "POST",
        headers: { Authorization: `Bearer ${authToken}` },
        body,
      }).catch(() => {})
    },
    [getToken, id],
  )

  useEffect(() => {
    load(id)
  }, [id, load])

  const handleSend = useCallback(
    async (message: string, files: File[]) => {
      const token = await getToken()
      if (!token) return

      // Clear previous error on new send
      setStreamError(null)

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
      setToolCall(null)
      setStreamingSources([])
      setIngestProgress(null)

      await startStream(id, message, token, files.length ? files : undefined)
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
    <div className="flex-1 flex min-h-0">
      <div className="flex-1 flex flex-col min-h-0">
        <MessageList
          messages={messages}
          streamingContent={streamingContent}
          agentStatus={agentStatus}
          toolCall={toolCall}
          streamingSources={streamingSources}
          isStreaming={isStreaming}
        />
        {toolCall?.tool_name === "document_finder" &&
          toolCall?.status === "ingesting" && (
            <DocumentIngestionBanner
              message={toolCall.message ?? "Ingesting document..."}
              tool_name={toolCall.tool_name}
            />
          )}
        {confirmationRequest && (
          <ConfirmationBanner
            request={confirmationRequest}
            onConfirm={() => sendConfirmation("yes", confirmationRequest.token)}
            onCancel={() => sendConfirmation("no", confirmationRequest.token)}
          />
        )}
        <InputBar
          onSend={handleSend}
          disabled={isStreaming}
          ingestProgress={ingestProgress}
          streamError={streamError}
        />
      </div>
      <DocumentPanel
        conversationId={id}
        isOpen={isPanelOpen}
        onToggle={() => setIsPanelOpen((v) => !v)}
      />
    </div>
  )
}
