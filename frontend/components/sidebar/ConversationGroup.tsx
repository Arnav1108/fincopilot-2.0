import ConversationItem from "./ConversationItem"
import type { ConversationRead } from "@/lib/types"

interface Props {
  label: string
  conversations: ConversationRead[]
  activeId: string | undefined
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  onClick: (id: string) => void
}

export default function ConversationGroup({
  label,
  conversations,
  activeId,
  onRename,
  onDelete,
  onClick,
}: Props) {
  if (conversations.length === 0) return null

  return (
    <div className="mb-2">
      <p className="px-2 py-1 text-xs font-medium text-muted-foreground uppercase tracking-wide">
        {label}
      </p>
      {conversations.map((conv) => (
        <ConversationItem
          key={conv.id}
          conversation={conv}
          isActive={conv.id === activeId}
          onRename={onRename}
          onDelete={onDelete}
          onClick={onClick}
        />
      ))}
    </div>
  )
}
