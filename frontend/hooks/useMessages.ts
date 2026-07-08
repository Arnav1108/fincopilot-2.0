"use client"

import { useCallback, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { listMessages } from "@/lib/api"
import { ApiError } from "@/lib/api"
import type { MessageRead } from "@/lib/types"

interface LoadOptions {
  /** Skip the isLoading flip — used for background reloads after a stream so
   *  the already-rendered transcript isn't replaced by a loading screen. */
  silent?: boolean
}

interface UseMessagesResult {
  messages: MessageRead[]
  isLoading: boolean
  notFound: boolean
  error: string | null
  load: (conversationId: string, opts?: LoadOptions) => Promise<MessageRead[] | null>
  addMessage: (msg: MessageRead) => void
}

export default function useMessages(): UseMessagesResult {
  const { getToken } = useAuth()
  const [messages, setMessages] = useState<MessageRead[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (conversationId: string, opts?: LoadOptions): Promise<MessageRead[] | null> => {
      if (!opts?.silent) setIsLoading(true)
      setNotFound(false)
      setError(null)
      try {
        const token = await getToken()
        if (!token) return null
        const data = await listMessages(token, conversationId)
        setMessages(data)
        return data
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          setNotFound(true)
        } else {
          setError("Failed to load this conversation. Check your connection and try again.")
        }
        return null
      } finally {
        if (!opts?.silent) setIsLoading(false)
      }
    },
    [getToken],
  )

  const addMessage = useCallback((msg: MessageRead) => {
    setMessages((prev) => [...prev, msg])
  }, [])

  return { messages, isLoading, notFound, error, load, addMessage }
}
