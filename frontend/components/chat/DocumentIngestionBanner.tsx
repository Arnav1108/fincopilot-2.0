"use client"

import { useEffect, useState } from "react"

interface Props {
  message: string
  tool_name: string
}

export default function DocumentIngestionBanner({ message, tool_name }: Props) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  if (tool_name !== "document_finder") return null

  return (
    <div className="relative overflow-hidden border-t border-primary/20 bg-primary/5">
      {/* shimmer overlay — pulses a brighter band across the bg */}
      <div className="pointer-events-none absolute inset-0 animate-pulse bg-gradient-to-r from-transparent via-primary/10 to-transparent" />
      <div className="relative mx-auto flex max-w-3xl items-center justify-between px-4 py-2.5 text-sm text-foreground">
        <div className="flex min-w-0 items-center gap-2">
          <span aria-hidden>⬇️</span>
          <span className="h-2 w-2 flex-shrink-0 animate-pulse rounded-full bg-primary" />
          <span className="truncate">{message}</span>
        </div>
        <span className="ml-4 flex-shrink-0 font-mono text-xs text-primary">
          {elapsed}s
        </span>
      </div>
    </div>
  )
}
