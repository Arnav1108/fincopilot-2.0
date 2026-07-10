"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { useConversations } from "@/hooks/useConversations"
import BrandMark from "@/components/BrandMark"
import ExamplePrompts from "@/components/chat/ExamplePrompts"
import TranscriptSkeleton from "@/components/chat/TranscriptSkeleton"

export default function ChatPage() {
  const router = useRouter()
  const { conversations, isLoading, create } = useConversations()
  const [isCreating, setIsCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  useEffect(() => {
    if (!isLoading && conversations.length > 0) {
      router.replace(`/chat/${conversations[0].id}`)
    }
  }, [isLoading, conversations, router])

  // Create a conversation and hand the chosen prompt to it via ?q= — the
  // conversation page auto-sends it on first load.
  const startWithPrompt = async (prompt: string) => {
    if (isCreating) return
    setIsCreating(true)
    setCreateError(null)
    try {
      const conv = await create()
      router.push(`/chat/${conv.id}?q=${encodeURIComponent(prompt)}`)
    } catch {
      setCreateError("Couldn't start a conversation. Please try again.")
      setIsCreating(false)
    }
  }

  const startBlank = async () => {
    if (isCreating) return
    setIsCreating(true)
    setCreateError(null)
    try {
      const conv = await create()
      router.push(`/chat/${conv.id}`)
    } catch {
      setCreateError("Couldn't start a conversation. Please try again.")
      setIsCreating(false)
    }
  }

  if (isLoading || conversations.length > 0) {
    return <TranscriptSkeleton />
  }

  return (
    <div className="relative flex-1 overflow-y-auto">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_50%_35%_at_50%_20%,hsl(172_66%_50%/0.07),transparent)]"
      />
      <div className="relative mx-auto flex min-h-full max-w-2xl flex-col items-center justify-center gap-8 px-4 py-12">
        <div className="flex flex-col items-center gap-3 text-center">
          <BrandMark size={44} />
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            What can I help you research?
          </h1>
          <p className="max-w-sm text-sm text-muted-foreground">
            Ask about a company, filing, or market event — or start from one of
            these.
          </p>
        </div>

        <ExamplePrompts onSelect={startWithPrompt} disabled={isCreating} />

        <button
          type="button"
          onClick={startBlank}
          disabled={isCreating}
          className="rounded-full bg-primary px-5 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isCreating ? "Starting…" : "Start a new chat"}
        </button>

        {createError && (
          <p role="alert" className="text-xs text-destructive">
            {createError}
          </p>
        )}
      </div>
    </div>
  )
}
