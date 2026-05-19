"use client"

import { cn } from "@/lib/utils"
import type { MessageRead, Source } from "@/lib/types"

// Placeholder — replaced by components/chat/AgentStatus.tsx in Step 17
function AgentStatus({ node }: { node: string }) {
  return (
    <span className="text-xs text-muted-foreground">
      {node}...
    </span>
  )
}

// Placeholder — replaced by components/chat/SourceList.tsx in Step 18
function SourceList({ sources }: { sources: Source[] }) {
  return (
    <div className="mt-2 space-y-0.5">
      <p className="text-xs font-medium text-muted-foreground">Sources</p>
      {sources.map((s, i) => (
        <a
          key={i}
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block text-xs text-muted-foreground underline hover:text-foreground truncate"
        >
          {s.title}
        </a>
      ))}
    </div>
  )
}

interface Props {
  message: MessageRead
  agentStatus?: string | null
  sources?: Source[]
  isStreaming?: boolean
}

export default function MessageBubble({ message, agentStatus, sources, isStreaming }: Props) {
  const isUser = message.role === "user"
  const isEmpty = message.content === ""
  const hasSources = !isStreaming && sources && sources.length > 0

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="bg-primary text-primary-foreground rounded-2xl px-4 py-2 max-w-[70%] text-sm">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-start gap-1 max-w-[80%]">
      <div className={cn("text-sm text-foreground", isEmpty && "text-muted-foreground")}>
        {isEmpty ? "..." : message.content}
      </div>
      {agentStatus && <AgentStatus node={agentStatus} />}
      {hasSources && <SourceList sources={sources!} />}
    </div>
  )
}
