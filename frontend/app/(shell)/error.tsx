"use client"

import { useEffect } from "react"

export default function ShellError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error("[shell error boundary]", error)
  }, [error])

  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3 px-4">
      <p className="text-foreground text-sm">Something went wrong rendering this page.</p>
      <p className="text-muted-foreground text-xs max-w-md text-center">
        The rest of the app is still running — you can try again or switch to
        another conversation from the sidebar.
      </p>
      <button
        type="button"
        onClick={reset}
        className="rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
      >
        Try again
      </button>
    </div>
  )
}
