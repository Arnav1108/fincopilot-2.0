"use client"

import { useCallback, useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { Button } from "@/components/ui/button"
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
import { listMemories, clearMemories } from "@/lib/api"
import type { MemoryRead } from "@/lib/types"

function formatRelativeAge(iso: string): string {
  const diffSec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diffSec < 60) return "just now"
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}h ago`
  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 30) return `${diffDay}d ago`
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

export default function MemoriesPage() {
  const { getToken } = useAuth()
  const [memories, setMemories] = useState<MemoryRead[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [clearError, setClearError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const load = useCallback(async () => {
    let cancelled = false
    setIsLoading(true)
    setError(null)
    const token = await getToken()
    if (!token) {
      setIsLoading(false)
      return () => { cancelled = true }
    }
    try {
      const data = await listMemories(token)
      if (!cancelled) setMemories(data.memories)
    } catch (e) {
      if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load memories")
    } finally {
      if (!cancelled) setIsLoading(false)
    }
    return () => { cancelled = true }
  }, [getToken])

  useEffect(() => {
    let cleanup: (() => void) | undefined
    load().then((fn) => { cleanup = fn })
    return () => { cleanup?.() }
  }, [load])

  const handleClearAll = async () => {
    setMemories([])
    setDialogOpen(false)
    setClearError(null)
    const token = await getToken()
    if (!token) return
    try {
      await clearMemories(token)
    } catch {
      setClearError("Failed to clear memories. Please try again.")
      load()
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-2xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-foreground">Memories</h1>
          {memories.length > 0 && (
            <Button variant="destructive" size="sm" onClick={() => setDialogOpen(true)}>
              Clear All
            </Button>
          )}
        </div>

        {/* Loading */}
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading memories…</p>
        )}

        {/* Fetch error */}
        {error && (
          <div className="flex items-center justify-between rounded-md border border-destructive/50 p-4 text-sm text-destructive">
            <span>{error}</span>
            <Button variant="outline" size="sm" onClick={load}>
              Retry
            </Button>
          </div>
        )}

        {/* Clear error */}
        {clearError && (
          <div className="rounded-md border border-destructive/50 p-4 text-sm text-destructive">
            {clearError}
          </div>
        )}

        {/* Empty state */}
        {!isLoading && !error && memories.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-12">
            The agent hasn&apos;t learned anything about you yet.
          </p>
        )}

        {/* Memory list */}
        {memories.map((memory) => (
          <div
            key={memory.id}
            className="rounded-lg border border-border bg-card p-4 flex items-start gap-3"
          >
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground shrink-0">
              {memory.fact_type}
            </span>
            <p className="text-sm text-foreground flex-1">{memory.content}</p>
            <span className="text-xs text-muted-foreground shrink-0">
              {formatRelativeAge(memory.created_at)}
            </span>
          </div>
        ))}
      </div>

      {/* Confirmation dialog */}
      <AlertDialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear all memories?</AlertDialogTitle>
            <AlertDialogDescription>
              The agent will no longer remember anything it has learned about you. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleClearAll}>Clear All</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
