"use client"

import { useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ApiError, deletePortfolio } from "@/lib/api"
import type { PortfolioRead } from "@/lib/types"

interface Props {
  portfolio: PortfolioRead
  onDeleted: () => void
}

export default function DeletePortfolioDialog({ portfolio, onDeleted }: Props) {
  const { getToken } = useAuth()
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleOpenChange = (value: boolean) => {
    if (!value) {
      setError(null)
      setLoading(false)
    }
    setOpen(value)
  }

  const handleDelete = async () => {
    const token = await getToken()
    if (!token) return
    setLoading(true)
    setError(null)
    try {
      await deletePortfolio(token, portfolio.id)
      setOpen(false)
      onDeleted()
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message)
      } else {
        setError("Something went wrong. Please try again.")
      }
    } finally {
      setLoading(false)
    }
  }

  const holdingCount = portfolio.holdings.length

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-muted-foreground hover:text-destructive transition-colors p-1"
        aria-label={`Delete portfolio ${portfolio.name}`}
      >
        <Trash2 size={16} />
      </button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="sm:max-w-md bg-card border-border">
          <DialogHeader>
            <DialogTitle>Delete Portfolio</DialogTitle>
            <DialogDescription>
              Delete &ldquo;{portfolio.name}&rdquo;? This will permanently remove the portfolio
              and all {holdingCount} holding{holdingCount !== 1 ? "s" : ""}. This cannot be undone.
            </DialogDescription>
          </DialogHeader>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={loading}
            >
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={loading}>
              {loading ? "Deleting…" : "Delete Portfolio"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
