"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useAuth } from "@clerk/nextjs"
import useMessages from "@/hooks/useMessages"
import useStream from "@/hooks/useStream"
import { useConversations } from "@/hooks/useConversations"
import MessageList from "@/components/chat/MessageList"
import InputBar from "@/components/chat/InputBar"
import DocumentIngestionBanner from "@/components/chat/DocumentIngestionBanner"
import ConfirmationBanner from "@/components/chat/ConfirmationBanner"
import DocumentPanel from "@/components/chat/DocumentPanel"
import TranscriptSkeleton from "@/components/chat/TranscriptSkeleton"
import { API_BASE, DEFAULT_MODEL } from "@/lib/api"
import { localId } from "@/lib/utils"
import type { ChartData, ConfirmationRequired, IngestProgress, Source, ToolCall } from "@/lib/types"

export default function ConversationPage({ params }: { params: { id: string } }) {
  const id = params.id
  const router = useRouter()
  const { getToken } = useAuth()
  // Prompt handed off by the empty state via ?q= — read once from the URL
  // (window, not useSearchParams, to avoid a Suspense boundary requirement).
  const [initialPrompt] = useState<string | null>(() =>
    typeof window === "undefined"
      ? null
      : new URLSearchParams(window.location.search).get("q"),
  )
  const autoSentRef = useRef(false)
  const { messages, isLoading, notFound, error: loadError, load, addMessage } = useMessages()
  const { bumpToTop, refresh } = useConversations()

  const [streamingContent, setStreamingContent] = useState("")
  const [agentStatus, setAgentStatus] = useState<string | null>(null)
  const [toolCall, setToolCall] = useState<ToolCall | null>(null)
  const [streamingSources, setStreamingSources] = useState<Source[]>([])
  const [ingestProgress, setIngestProgress] = useState<IngestProgress | null>(null)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [isPanelOpen, setIsPanelOpen] = useState(true)
  const [confirmationRequest, setConfirmationRequest] = useState<ConfirmationRequired | null>(null)
  const [streamingChartData, setStreamingChartData] = useState<ChartData | null>(null)
  // Bumped whenever the backend may have attached new documents, so the panel re-fetches
  const [docRefreshKey, setDocRefreshKey] = useState(0)

  // Ref tracks accumulated content so onDone never has a stale closure
  const streamingContentRef = useRef("")

  const clearStreamingState = useCallback(() => {
    streamingContentRef.current = ""
    setStreamingContent("")
    setAgentStatus(null)
    setToolCall(null)
    setStreamingSources([])
    setIngestProgress(null)
    setConfirmationRequest(null)
    setStreamingChartData(null)
  }, [])

  const { startStream, isStreaming, stop } = useStream({
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
        bumpToTop(id)
        // Reload first, clear after — the streaming bubble stays rendered until
        // the settled message is in place, so the answer never blinks out.
        await load(id, { silent: true })
        clearStreamingState()
        setDocRefreshKey((k) => k + 1)
        await refresh()
      },
      [bumpToTop, id, load, refresh, clearStreamingState],
    ),

    onError: useCallback(
      async (err: Error) => {
        console.error("[useStream] error", err)
        setStreamError(err.message)
        // The backend may have persisted a partial or complete reply before the
        // stream broke — re-fetch so whatever was saved is shown.
        await load(id, { silent: true })
        clearStreamingState()
      },
      [id, load, clearStreamingState],
    ),

    onIngestProgress: useCallback((progress: IngestProgress) => {
      setIngestProgress(progress)
    }, []),

    onIngestComplete: useCallback((_count: number) => {
      // Ingestion done; Phase 2 (agent) begins — clear progress indicator
      setIngestProgress(null)
      setDocRefreshKey((k) => k + 1)
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

    onChartData: useCallback((cd: ChartData) => {
      setStreamingChartData(cd)
    }, []),
  })

  const sendConfirmation = useCallback(
    async (answer: "yes" | "no", token: string) => {
      setConfirmationRequest(null)
      try {
        const authToken = await getToken()
        if (!authToken) throw new Error("Not authenticated.")
        const body = new FormData()
        body.append("message", `CONFIRM:${answer}:${token}`)
        body.append("model", DEFAULT_MODEL)
        const res = await fetch(`${API_BASE}/api/v1/conversations/${id}/stream`, {
          method: "POST",
          headers: { Authorization: `Bearer ${authToken}` },
          body,
        })
        if (!res.ok) throw new Error(`Confirmation failed (${res.status}).`)
        // The response is a short SSE ack stream (confirmed + done). Drain it
        // fully so the backend generator runs to completion.
        await res.text().catch(() => {})
      } catch (e) {
        setStreamError(
          e instanceof Error ? e.message : "Failed to send confirmation.",
        )
      }
    },
    [getToken, id],
  )

  useEffect(() => {
    load(id)
    return () => {
      // Abort any in-flight stream before switching conversations or
      // unmounting — otherwise it keeps streaming in the background and its
      // stale onDone closure reloads the *previous* conversation.
      stop()
      clearStreamingState()
      setStreamError(null)
    }
  }, [id, load, stop, clearStreamingState])

  const handleStop = useCallback(async () => {
    const partial = streamingContentRef.current
    stop()
    // The backend usually persists the assistant message even when the client
    // aborts — reload to pick it up. If it didn't, keep the partial text as a
    // local settled message instead of silently discarding it.
    const fresh = await load(id, { silent: true })
    const lastIsAssistant =
      fresh != null && fresh.length > 0 && fresh[fresh.length - 1].role === "assistant"
    if (partial && !lastIsAssistant) {
      addMessage({
        id: localId(),
        conversation_id: id,
        role: "assistant",
        content: partial,
        created_at: new Date().toISOString(),
      })
    }
    clearStreamingState()
  }, [stop, load, id, addMessage, clearStreamingState])

  const handleSend = useCallback(
    async (message: string, files: File[]) => {
      const token = await getToken()
      if (!token) return

      // Clear previous error on new send
      setStreamError(null)

      addMessage({
        id: localId(),
        conversation_id: id,
        role: "user",
        content: message,
        created_at: new Date().toISOString(),
      })
      clearStreamingState()

      await startStream(id, message, token, files.length ? files : undefined)
    },
    [getToken, addMessage, id, startStream, clearStreamingState],
  )

  // Auto-send the empty-state prompt once the transcript has loaded, then
  // strip ?q= so a reload doesn't re-send it.
  useEffect(() => {
    if (!initialPrompt || autoSentRef.current || isLoading || notFound) return
    autoSentRef.current = true
    router.replace(`/chat/${id}`, { scroll: false })
    handleSend(initialPrompt, [])
  }, [initialPrompt, isLoading, notFound, handleSend, id, router])

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

  if (loadError && messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3">
        <p className="text-foreground text-sm">{loadError}</p>
        <button
          type="button"
          onClick={() => load(id)}
          className="rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="flex-1 flex min-h-0">
      <div className="flex-1 flex flex-col min-h-0">
        {isLoading ? (
          <TranscriptSkeleton />
        ) : (
          <>
            <MessageList
              messages={messages}
              streamingContent={streamingContent}
              agentStatus={agentStatus}
              toolCall={toolCall}
              streamingSources={streamingSources}
              isStreaming={isStreaming}
              streamingChartData={streamingChartData}
              onSuggestion={(prompt) => {
                if (!isStreaming) handleSend(prompt, [])
              }}
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
              onStop={handleStop}
              disabled={isStreaming}
              ingestProgress={ingestProgress}
              streamError={streamError}
            />
          </>
        )}
      </div>
      <DocumentPanel
        conversationId={id}
        isOpen={isPanelOpen}
        onToggle={() => setIsPanelOpen((v) => !v)}
        refreshKey={docRefreshKey}
      />
    </div>
  )
}
