"use client"

import { BarChart3, FileText, ShieldAlert, TrendingUp } from "lucide-react"
import { cn } from "@/lib/utils"

export const EXAMPLE_PROMPTS = [
  {
    icon: BarChart3,
    label: "Compare filings",
    prompt: "Compare NVDA and AMD's latest 10-K filings",
  },
  {
    icon: FileText,
    label: "Summarize earnings",
    prompt: "Summarize Apple's most recent earnings call",
  },
  {
    icon: TrendingUp,
    label: "Track a metric",
    prompt: "How has Tesla's gross margin trended over the last 3 years?",
  },
  {
    icon: ShieldAlert,
    label: "Surface risks",
    prompt: "What are the biggest risk factors in Microsoft's latest 10-K?",
  },
] as const

interface Props {
  onSelect: (prompt: string) => void
  disabled?: boolean
}

export default function ExamplePrompts({ onSelect, disabled }: Props) {
  return (
    <div className="grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
      {EXAMPLE_PROMPTS.map(({ icon: Icon, label, prompt }) => (
        <button
          key={prompt}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(prompt)}
          className={cn(
            "group flex flex-col items-start gap-1.5 rounded-xl border border-border bg-card/60 px-4 py-3 text-left transition-colors",
            "hover:border-primary/40 hover:bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            disabled && "cursor-not-allowed opacity-60",
          )}
        >
          <span className="flex items-center gap-2 text-xs font-medium text-primary">
            <Icon size={14} aria-hidden />
            {label}
          </span>
          <span className="text-[13px] leading-snug text-muted-foreground transition-colors group-hover:text-foreground">
            {prompt}
          </span>
        </button>
      ))}
    </div>
  )
}
