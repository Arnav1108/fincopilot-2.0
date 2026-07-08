"use client"

import { useRef, useState } from "react"
import { Pencil, Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
  const [confirmOpen, setConfirmOpen] = useState(false)
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

  const handleRowKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (isEditing) return
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      onClick(conversation.id)
    }
    if (e.key === "F2") {
      e.preventDefault()
      startEdit()
    }
  }

  // Visible on hover AND on keyboard focus, so focusable controls are never invisible
  const actionButtonClasses =
    "shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity"

  return (
    <>
      <div
        data-testid="conversation-item"
        data-conv-id={conversation.id}
        role="button"
        tabIndex={isEditing ? -1 : 0}
        aria-label={`Open conversation: ${conversation.title}`}
        aria-current={isActive ? "page" : undefined}
        className={cn(
          "group flex items-center gap-1 px-3 py-1.5 rounded-md cursor-pointer text-[13px] select-none",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          isActive
            ? "bg-muted text-foreground"
            : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
        )}
        onClick={() => !isEditing && onClick(conversation.id)}
        onDoubleClick={startEdit}
        onKeyDown={handleRowKeyDown}
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
          <>
            <button
              className={cn(actionButtonClasses, "text-muted-foreground hover:text-foreground")}
              onClick={(e) => { e.stopPropagation(); startEdit() }}
              aria-label={`Rename conversation: ${conversation.title}`}
              title="Rename"
            >
              <Pencil size={13} />
            </button>
            <button
              className={cn(actionButtonClasses, "text-muted-foreground hover:text-destructive")}
              onClick={(e) => { e.stopPropagation(); setConfirmOpen(true) }}
              aria-label={`Delete conversation: ${conversation.title}`}
              title="Delete"
            >
              <Trash2 size={14} />
            </button>
          </>
        )}
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              &ldquo;{conversation.title}&rdquo; and all of its messages and
              documents will be permanently deleted. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => onDelete(conversation.id)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
