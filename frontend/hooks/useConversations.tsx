"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react"
import { useAuth } from "@clerk/nextjs"
import {
  createConversation as apiCreate,
  listConversations as apiList,
  renameConversation as apiRename,
  deleteConversation as apiDelete,
} from "@/lib/api"
import type { ConversationRead } from "@/lib/types"

interface ConversationsContextValue {
  conversations: ConversationRead[]
  isLoading: boolean
  error: string | null
  create: () => Promise<ConversationRead>
  rename: (id: string, title: string) => Promise<void>
  remove: (id: string) => Promise<void>
  bumpToTop: (id: string) => void
}

const ConversationsContext = createContext<ConversationsContextValue | null>(null)

export function ConversationsProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth()
  const [conversations, setConversations] = useState<ConversationRead[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setIsLoading(true)
      const token = await getToken()
      if (!token) return
      const data = await apiList(token)
      setConversations(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load conversations")
    } finally {
      setIsLoading(false)
    }
  }, [getToken])

  useEffect(() => {
    load()
  }, [load])

  const create = useCallback(async (): Promise<ConversationRead> => {
    const token = await getToken()
    if (!token) throw new Error("Not authenticated")
    const conv = await apiCreate(token)
    setConversations((prev) => [conv, ...prev])
    return conv
  }, [getToken])

  const rename = useCallback(async (id: string, title: string): Promise<void> => {
    const token = await getToken()
    if (!token) throw new Error("Not authenticated")
    const updated = await apiRename(token, id, title)
    setConversations((prev) => prev.map((c) => (c.id === id ? updated : c)))
  }, [getToken])

  const remove = useCallback(async (id: string): Promise<void> => {
    const token = await getToken()
    if (!token) throw new Error("Not authenticated")
    await apiDelete(token, id)
    setConversations((prev) => prev.filter((c) => c.id !== id))
  }, [getToken])

  const bumpToTop = useCallback((id: string) => {
    setConversations((prev) => {
      const idx = prev.findIndex((c) => c.id === id)
      if (idx <= 0) return prev
      const next = [...prev]
      const [conv] = next.splice(idx, 1)
      return [{ ...conv, updated_at: new Date().toISOString() }, ...next]
    })
  }, [])

  return (
    <ConversationsContext.Provider
      value={{ conversations, isLoading, error, create, rename, remove, bumpToTop }}
    >
      {children}
    </ConversationsContext.Provider>
  )
}

export function useConversations(): ConversationsContextValue {
  const ctx = useContext(ConversationsContext)
  if (!ctx) throw new Error("useConversations must be used within ConversationsProvider")
  return ctx
}
