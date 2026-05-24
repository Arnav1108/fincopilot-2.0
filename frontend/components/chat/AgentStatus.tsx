import type { ToolCall } from "@/lib/types"

interface Props {
  node: string
  toolCall?: ToolCall
}

function getToolDisplay(tool_name: string, status: string): { icon: string; label: string } {
  if (tool_name === "document_finder") {
    if (status === "ingesting") return { icon: "⬇️", label: "Ingesting document..." }
    if (status === "complete")  return { icon: "✅", label: "Document ready" }
    return { icon: "🔎", label: "Finding document" }
  }
  const map: Record<string, { icon: string; label: string }> = {
    document_retrieval:   { icon: "🔍", label: "Searching documents" },
    web_search:           { icon: "🌐", label: "Searching web" },
    financial_data:       { icon: "📈", label: "Fetching market data" },
    company_comparator:   { icon: "📊", label: "Comparing companies" },
    financial_calculator: { icon: "🧮", label: "Calculating" },
  }
  return map[tool_name] ?? { icon: "⚙️", label: tool_name }
}

export default function AgentStatus({ node, toolCall }: Props) {
  const toolDisplay = toolCall ? getToolDisplay(toolCall.tool_name, toolCall.status) : null

  return (
    <div data-testid="agent-status" className="flex flex-col gap-0.5 mt-1">
      <span className="text-xs text-muted-foreground">
        {node}<span className="animate-pulse">...</span>
      </span>
      {toolDisplay && (
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>{toolDisplay.icon}</span>
          <span>{toolDisplay.label}</span>
          {toolCall!.status === "ingesting" && (
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
          )}
        </span>
      )}
    </div>
  )
}
