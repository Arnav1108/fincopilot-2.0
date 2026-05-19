"use client"

import { useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { Plus, Settings } from "lucide-react"
import { useConversations } from "@/hooks/useConversations"
import ConversationGroup from "./ConversationGroup"
import type { ConversationRead } from "@/lib/types"

const MS_PER_DAY = 86_400_000

function groupByDate(conversations: ConversationRead[]): Record<string, ConversationRead[]> {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfYesterday = new Date(startOfToday.getTime() - MS_PER_DAY)
  const start7DaysAgo = new Date(startOfToday.getTime() - 7 * MS_PER_DAY)
  const start30DaysAgo = new Date(startOfToday.getTime() - 30 * MS_PER_DAY)

  const groups: Record<string, ConversationRead[]> = {
    Today: [],
    Yesterday: [],
    "Previous 7 Days": [],
    "Previous 30 Days": [],
    Older: [],
  }

  for (const conv of conversations) {
    const d = new Date(conv.updated_at)
    if (d >= startOfToday) groups["Today"].push(conv)
    else if (d >= startOfYesterday) groups["Yesterday"].push(conv)
    else if (d >= start7DaysAgo) groups["Previous 7 Days"].push(conv)
    else if (d >= start30DaysAgo) groups["Previous 30 Days"].push(conv)
    else groups["Older"].push(conv)
  }

  return groups
}

export default function Sidebar() {
  const router = useRouter()
  const params = useParams()
  const activeId = typeof params?.id === "string" ? params.id : undefined
  const { conversations, isLoading, create, rename, remove } = useConversations()
  const [settingsOpen, setSettingsOpen] = useState(false)

  const handleNew = async () => {
    try {
      const conv = await create()
      router.push(`/chat/${conv.id}`)
    } catch {}
  }

  const handleRename = async (id: string, title: string) => {
    try {
      await rename(id, title)
    } catch {}
  }

  const handleDelete = async (id: string) => {
    try {
      await remove(id)
      if (id === activeId) {
        const remaining = conversations.filter((c) => c.id !== id)
        router.push(remaining.length > 0 ? `/chat/${remaining[0].id}` : "/chat")
      }
    } catch {}
  }

  const groups = groupByDate(conversations)

  return (
    <>
      <aside className="flex flex-col w-[260px] shrink-0 h-full bg-secondary shadow-[1px_0_0_0_hsl(var(--border))]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-4">
          <span className="font-semibold text-[15px] tracking-tight text-foreground">
            FinCopilot
          </span>
          <button
            onClick={handleNew}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            aria-label="New chat"
          >
            <Plus size={18} />
          </button>
        </div>

        {/* Conversation list */}
        <div className="flex-1 overflow-y-auto px-2 py-1">
          {isLoading ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">Loading…</p>
          ) : conversations.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">No conversations yet.</p>
          ) : (
            Object.entries(groups).map(([label, convs]) => (
              <ConversationGroup
                key={label}
                label={label}
                conversations={convs}
                activeId={activeId}
                onRename={handleRename}
                onDelete={handleDelete}
                onClick={(id) => router.push(`/chat/${id}`)}
              />
            ))
          )}
        </div>

        {/* Footer */}
        <div className="px-3 py-4">
          <button
            onClick={() => setSettingsOpen(true)}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            aria-label="Settings"
          >
            <Settings size={16} />
            <span>Settings</span>
          </button>
        </div>
      </aside>

      {/* SettingsModal will be rendered here in Phase 5 */}
      {settingsOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-card border border-border rounded-xl p-6 text-foreground text-sm">
            Settings coming soon.{" "}
            <button
              className="underline text-muted-foreground"
              onClick={() => setSettingsOpen(false)}
            >
              Close
            </button>
          </div>
        </div>
      )}
    </>
  )
}
