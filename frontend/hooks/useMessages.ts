"use client"

import { useCallback, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { listMessages } from "@/lib/api"
import { ApiError } from "@/lib/api"
import type { MessageRead } from "@/lib/types"

interface UseMessagesResult {
  messages: MessageRead[]
  isLoading: boolean
  notFound: boolean
  load: (conversationId: string) => Promise<void>
  addMessage: (msg: MessageRead) => void
}

export default function useMessages(): UseMessagesResult {
  const { getToken } = useAuth()
  const [messages, setMessages] = useState<MessageRead[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [notFound, setNotFound] = useState(false)

  const load = useCallback(
    async (conversationId: string) => {
      setIsLoading(true)
      setNotFound(false)
      try {
        const token = await getToken()
        if (!token) return
        const data = await listMessages(token, conversationId)
        setMessages(data)
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          setNotFound(true)
        }
      } finally {
        setIsLoading(false)
      }
    },
    [getToken],
  )

  const addMessage = useCallback((msg: MessageRead) => {
    setMessages((prev) => [...prev, msg])
  }, [])

  return { messages, isLoading, notFound, load, addMessage }
}
