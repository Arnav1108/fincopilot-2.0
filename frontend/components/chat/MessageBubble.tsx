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
