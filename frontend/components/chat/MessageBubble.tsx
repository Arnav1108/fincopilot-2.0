"use client"

import { cn } from "@/lib/utils"
import type { MessageRead, Source } from "@/lib/types"
import AgentStatus from "./AgentStatus"
import SourceList from "./SourceList"

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
        <div className="bg-muted text-foreground rounded-3xl px-4 py-3 max-w-[85%] text-sm leading-relaxed">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className={cn("text-sm text-foreground leading-relaxed", isEmpty && "text-muted-foreground")}>
        {isEmpty ? "..." : message.content}
      </div>
      {agentStatus && <AgentStatus node={agentStatus} />}
      {hasSources && <SourceList sources={sources!} />}
    </div>
  )
}
