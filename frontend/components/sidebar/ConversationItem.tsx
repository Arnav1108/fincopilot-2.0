"use client"

import { useRef, useState } from "react"
import { Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ConversationRead } from "@/lib/types"

interface Props {
  conversation: ConversationRead
  isActive: boolean
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  onClick: (id: string) => void
}

export default function ConversationItem({
  conversation,
  isActive,
  onRename,
  onDelete,
  onClick,
}: Props) {
  const [isEditing, setIsEditing] = useState(false)
  const [editValue, setEditValue] = useState(conversation.title)
  const inputRef = useRef<HTMLInputElement>(null)

  const startEdit = () => {
    setEditValue(conversation.title)
    setIsEditing(true)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const commitEdit = () => {
    const trimmed = editValue.trim()
    if (trimmed && trimmed !== conversation.title) {
      onRename(conversation.id, trimmed)
    }
    setIsEditing(false)
  }

  const cancelEdit = () => {
    setEditValue(conversation.title)
    setIsEditing(false)
  }

  return (
    <div
      className={cn(
        "group flex items-center gap-1 px-2 py-1.5 rounded-md cursor-pointer text-sm select-none",
        isActive
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
      )}
      onClick={() => !isEditing && onClick(conversation.id)}
      onDoubleClick={startEdit}
    >
      {isEditing ? (
        <input
          ref={inputRef}
          className="flex-1 min-w-0 bg-transparent border-none outline-none text-foreground text-sm"
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); commitEdit() }
            if (e.key === "Escape") { e.preventDefault(); cancelEdit() }
          }}
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span className="flex-1 truncate">{conversation.title}</span>
      )}
      {!isEditing && (
        <button
          className="shrink-0 opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-opacity"
          onClick={(e) => { e.stopPropagation(); onDelete(conversation.id) }}
          aria-label="Delete conversation"
        >
          <Trash2 size={14} />
        </button>
      )}
    </div>
  )
}
