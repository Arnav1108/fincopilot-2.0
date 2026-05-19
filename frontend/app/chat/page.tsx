"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { useConversations } from "@/hooks/useConversations"

export default function ChatPage() {
  const router = useRouter()
  const { conversations, isLoading } = useConversations()

  useEffect(() => {
    if (!isLoading && conversations.length > 0) {
      router.replace(`/chat/${conversations[0].id}`)
    }
  }, [isLoading, conversations, router])

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <span className="text-muted-foreground text-sm">Loading…</span>
      </div>
    )
  }

  if (conversations.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-muted-foreground text-sm">
          Ask me anything about a company, filing, or market event.
        </p>
      </div>
    )
  }

  return null
}
