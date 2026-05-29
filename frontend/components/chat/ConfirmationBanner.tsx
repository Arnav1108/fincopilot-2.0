"use client"

import type { ConfirmationRequired } from "@/lib/types"

interface Props {
  request: ConfirmationRequired
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmationBanner({ request, onConfirm, onCancel }: Props) {
  return (
    <div className="border-t border-amber-200 bg-amber-50 px-4 py-3">
      <div className="mx-auto flex max-w-3xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-2 text-sm text-amber-800">
          <span aria-hidden className="mt-0.5 flex-shrink-0">📄</span>
          <span>
            Found: <strong>{request.description}</strong>. Download and analyze?
          </span>
        </div>
        <div className="flex flex-shrink-0 gap-2">
          <button
            onClick={onConfirm}
            className="rounded-md bg-amber-700 px-4 py-1.5 text-sm font-medium text-white hover:bg-amber-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-700"
          >
            Yes, download
          </button>
          <button
            onClick={onCancel}
            className="rounded-md border border-amber-300 bg-white px-4 py-1.5 text-sm font-medium text-amber-800 hover:bg-amber-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-700"
          >
            No, skip
          </button>
        </div>
      </div>
    </div>
  )
}
