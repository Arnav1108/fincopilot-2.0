"use client"

import { useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { PanelLeft, Search, Settings, SquarePen } from "lucide-react"
import { UserButton } from "@clerk/nextjs"
import { useConversations } from "@/hooks/useConversations"
import ConversationItem from "./ConversationItem"
import SettingsModal from "@/components/settings/SettingsModal"
import BrandMark from "@/components/BrandMark"

// Clerk popover theming is centralized on ClerkProvider in app/layout.tsx.

function ConversationListSkeleton() {
  const widths = ["w-11/12", "w-3/4", "w-5/6", "w-2/3", "w-4/5", "w-3/5"]
  return (
    <div className="space-y-1.5 px-3 py-2" aria-hidden>
      {widths.map((w, i) => (
        <div key={i} className={`h-6 animate-pulse rounded-md bg-muted/60 ${w}`} />
      ))}
    </div>
  )
}

export default function Sidebar() {
  const router = useRouter()
  const params = useParams()

  const activeId = typeof params?.id === "string" ? params.id : undefined
  const { conversations, isLoading, error, create, rename, remove, refresh } = useConversations()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [isCollapsed, setIsCollapsed] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")

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

  if (isCollapsed) {
    return (
      <>
        <aside className="flex flex-col w-[52px] shrink-0 h-full bg-background border-r border-border transition-all duration-200">
          <div className="flex flex-col items-center gap-2 px-1.5 py-3">
            <BrandMark size={22} />
            <button
              onClick={() => setIsCollapsed(false)}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              aria-label="Expand sidebar"
              title="Expand sidebar"
            >
              <PanelLeft size={16} />
            </button>
          </div>

          <div className="flex-1" />

          <div className="flex flex-col items-center gap-2 px-1.5 py-3 border-t border-border">
            <button
              onClick={handleNew}
              data-testid="new-chat-button"
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              aria-label="New chat"
              title="New chat"
            >
              <SquarePen size={15} />
            </button>
            <button
              onClick={() => setSettingsOpen(true)}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              aria-label="Settings"
              title="Settings"
            >
              <Settings size={15} />
            </button>
            <UserButton />
          </div>
        </aside>

        <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      </>
    )
  }

  return (
    <>
      <aside className="flex flex-col w-[260px] shrink-0 h-full bg-background border-r border-border transition-all duration-200">
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-3">
          <span className="flex items-center gap-2">
            <BrandMark size={20} />
            <span className="font-semibold text-sm tracking-tight text-foreground">FinCopilot</span>
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => {
                if (searchOpen) { setSearchOpen(false); setSearchQuery("") } else { setSearchOpen(true) }
              }}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              aria-label="Search conversations"
              title="Search conversations"
            >
              <Search size={16} />
            </button>
            <button
              onClick={() => setIsCollapsed(true)}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
            >
              <PanelLeft size={16} />
            </button>
          </div>
        </div>

        {searchOpen && (
          <div className="px-3 pb-2">
            <input
              autoFocus
              type="text"
              placeholder="Search conversations..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Escape") { setSearchOpen(false); setSearchQuery("") } }}
              className="w-full rounded-md border border-border bg-muted px-3 py-1.5 text-[13px] text-foreground placeholder:text-muted-foreground outline-none focus:ring-1 focus:ring-border"
            />
          </div>
        )}

        {/* Conversation list */}
        <div data-testid="conversation-list" className="flex-1 overflow-y-auto px-2 py-1 scrollbar-thin">
          {(() => {
            if (isLoading) {
              return <ConversationListSkeleton />
            }
            if (error && conversations.length === 0) {
              return (
                <div className="flex flex-col items-start gap-2 px-3 py-2">
                  <p className="text-xs text-muted-foreground">
                    Couldn&apos;t load conversations.
                  </p>
                  <button
                    type="button"
                    onClick={() => refresh()}
                    className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                  >
                    Retry
                  </button>
                </div>
              )
            }
            const filteredConversations = searchQuery.trim()
              ? conversations.filter((c) => c.title.toLowerCase().includes(searchQuery.toLowerCase()))
              : conversations
            if (filteredConversations.length === 0 && searchQuery.trim()) {
              return <p className="px-3 py-2 text-xs text-muted-foreground">No conversations found.</p>
            }
            if (filteredConversations.length === 0) {
              return <p className="px-3 py-2 text-xs text-muted-foreground">No conversations yet.</p>
            }
            return filteredConversations.map((conv) => (
              <ConversationItem
                key={conv.id}
                conversation={conv}
                isActive={conv.id === activeId}
                onRename={handleRename}
                onDelete={handleDelete}
                onClick={(id) => router.push(`/chat/${id}`)}
              />
            ))
          })()}
        </div>

        {/* Footer */}
        <div className="px-2 py-3 border-t border-border space-y-0.5">
          <button
            onClick={handleNew}
            data-testid="new-chat-button"
            className="flex items-center gap-2.5 w-full px-3 py-2 rounded-md text-[13px] text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <SquarePen size={15} />
            <span>New chat</span>
          </button>

          <button
            onClick={() => setSettingsOpen(true)}
            className="flex items-center gap-2.5 w-full px-3 py-2 rounded-md text-[13px] text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <Settings size={15} />
            <span>Settings</span>
          </button>

          <div className="px-3 py-2 mb-2">
            <UserButton />
          </div>
        </div>
      </aside>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  )
}
