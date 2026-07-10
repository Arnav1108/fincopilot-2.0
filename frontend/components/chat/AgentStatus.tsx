"use client"

import { useEffect, useRef, useState } from "react"
import { cn } from "@/lib/utils"
import type { ToolCall } from "@/lib/types"

const PIPELINE: { key: string; label: string }[] = [
  { key: "router_node",        label: "Routing" },
  { key: "tool_selector_node", label: "Selecting tool" },
  { key: "planner_node",       label: "Planning" },
  { key: "executor_node",      label: "Executing" },
  { key: "synthesizer_node",   label: "Synthesizing" },
]

function getToolIcon(tool_name: string): string {
  if (tool_name === "document_finder") return "🔎"
  const map: Record<string, string> = {
    document_retrieval:   "🔍",
    web_search:           "🌐",
    financial_data:       "📈",
    company_comparator:   "📊",
    financial_calculator: "🧮",
  }
  return map[tool_name] ?? "⚙️"
}

function getToolLabel(tool_name: string): string {
  if (tool_name === "document_finder") return "Finding document"
  const map: Record<string, string> = {
    document_retrieval:   "Searching documents",
    web_search:           "Searching web",
    financial_data:       "Fetching market data",
    company_comparator:   "Comparing companies",
    financial_calculator: "Calculating",
  }
  return map[tool_name] ?? tool_name
}

interface Props {
  node: string
  toolCall?: ToolCall
}

export default function AgentStatus({ node, toolCall }: Props) {
  const [completedNodes, setCompletedNodes] = useState<string[]>([])
  const prevNodeRef = useRef<string | null>(null)

  useEffect(() => {
    const prev = prevNodeRef.current
    if (prev && prev !== node) {
      setCompletedNodes((existing) =>
        existing.includes(prev) ? existing : [...existing, prev]
      )
    }
    prevNodeRef.current = node
  }, [node])

  const activeIndex = PIPELINE.findIndex((p) => p.key === node)
  const nodesToRender =
    activeIndex === -1
      ? PIPELINE.filter((p) => completedNodes.includes(p.key))
      : PIPELINE.slice(0, activeIndex + 1)

  return (
    <div data-testid="agent-status" className="flex flex-col gap-0 mt-1">
      {nodesToRender.map((pipelineNode, i) => {
        const isActive = pipelineNode.key === node
        const isLastRendered = i === nodesToRender.length - 1

        return (
          <div key={pipelineNode.key} className="flex items-start gap-2.5">
            <div className="flex flex-col items-center" style={{ width: 16 }}>
              {isActive ? (
                <div className="relative flex items-center justify-center">
                  <div className="h-2.5 w-2.5 rounded-full bg-primary" />
                  <div className="absolute h-2.5 w-2.5 rounded-full bg-primary animate-ping opacity-75" />
                </div>
              ) : (
                <div className="h-2.5 w-2.5 rounded-full bg-primary/70" />
              )}
              {!isLastRendered && (
                <div className="w-px flex-1 bg-primary/30 mt-0.5" style={{ minHeight: 16 }} />
              )}
            </div>
            <div className="pb-3">
              <span
                className={cn(
                  "text-xs font-medium",
                  isActive ? "text-primary" : "text-muted-foreground",
                )}
              >
                {pipelineNode.label}
              </span>
              {isActive && pipelineNode.key === "executor_node" && toolCall && (
                <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span>{getToolIcon(toolCall.tool_name)}</span>
                  <span>{getToolLabel(toolCall.tool_name)}</span>
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
