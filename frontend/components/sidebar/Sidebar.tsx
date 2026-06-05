"use client"

import { useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { PanelLeft, Search, Settings, SquarePen } from "lucide-react"
import { UserButton } from "@clerk/nextjs"
import { useConversations } from "@/hooks/useConversations"
import ConversationItem from "./ConversationItem"
import SettingsModal from "@/components/settings/SettingsModal"

const USER_BUTTON_APPEARANCE = {
  variables: {
    colorText: "#e2e8f0",
    colorTextSecondary: "#94a3b8",
    colorBackground: "hsl(var(--card))",
  },
  elements: {
    userButtonPopoverCard: "bg-card border border-border shadow-lg text-foreground",
    userButtonPopoverMain: "bg-card",
    userButtonPopoverUserPreview: "bg-card border-b border-border",
    userButtonPopoverUserPreviewTextContainer: "text-foreground",
    userButtonPopoverUserPreviewSecondaryIdentifier: "text-foreground/70",
    userButtonPopoverUserPreviewUnverifiedIdentifier: "text-foreground/70",
    userButtonPopoverActions: "bg-card",
    userButtonPopoverActionButton: "text-foreground hover:bg-muted",
    userButtonPopoverActionButtonText: "text-foreground",
    userButtonPopoverActionButtonIcon: "text-muted-foreground",
    userButtonPopoverFooter: "hidden",
    userButtonPopoverFooterPages: "bg-card",
  },
}

export default function Sidebar() {
  const router = useRouter()
  const params = useParams()

  const activeId = typeof params?.id === "string" ? params.id : undefined
  const { conversations, isLoading, create, rename, remove } = useConversations()
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
          <div className="flex flex-col items-center gap-1 px-1.5 py-3">
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
            <UserButton appearance={USER_BUTTON_APPEARANCE} />
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
          <span className="font-semibold text-sm text-foreground">FinCopilot</span>
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
        <div data-testid="conversation-list" className="flex-1 overflow-y-auto px-2 py-1 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar-track]:transparent">
          {(() => {
            if (isLoading) {
              return <p className="px-3 py-2 text-xs text-muted-foreground">Loading…</p>
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
            <UserButton appearance={USER_BUTTON_APPEARANCE} />
          </div>
        </div>
      </aside>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  )
}
