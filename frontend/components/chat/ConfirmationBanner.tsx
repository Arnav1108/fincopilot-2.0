"use client"

import type { ConfirmationRequired } from "@/lib/types"

interface Props {
  request: ConfirmationRequired
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmationBanner({ request, onConfirm, onCancel }: Props) {
  return (
    <div className="border-t border-border bg-card px-4 py-3">
      <div className="mx-auto flex max-w-3xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-2 text-sm text-foreground">
          <span aria-hidden className="mt-0.5 flex-shrink-0">📄</span>
          <span>
            Found: <strong>{request.description}</strong>. Download and analyze?
          </span>
        </div>
        <div className="flex flex-shrink-0 gap-2">
          <button
            onClick={onConfirm}
            className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Yes, download
          </button>
          <button
            onClick={onCancel}
            className="rounded-md border border-border bg-muted px-4 py-1.5 text-sm font-medium text-foreground hover:bg-muted/80"
          >
            No, skip
          </button>
        </div>
      </div>
    </div>
  )
}
