"use client"

import { useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { ApiError, createPortfolio } from "@/lib/api"
import type { PortfolioRead } from "@/lib/types"

interface Props {
  onCreated: (portfolio: PortfolioRead) => void
}

function parse422(body: string): string {
  try {
    const j = JSON.parse(body)
    if (Array.isArray(j.detail)) return j.detail[0].msg
    return String(j.detail)
  } catch {
    return body
  }
}

export default function CreatePortfolioDialog({ onCreated }: Props) {
  const { getToken } = useAuth()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleOpenChange = (value: boolean) => {
    if (!value) {
      setName("")
      setError(null)
      setLoading(false)
    }
    setOpen(value)
  }

  const handleSubmit = async () => {
    if (name.trim() === "") {
      setError("Name is required")
      return
    }
    const token = await getToken()
    if (!token) return
    setLoading(true)
    setError(null)
    try {
      const portfolio = await createPortfolio(token, name.trim())
      setOpen(false)
      onCreated(portfolio)
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        setError(parse422(e.message))
      } else {
        setError("Something went wrong. Please try again.")
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>New Portfolio</Button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="sm:max-w-md bg-card border-border">
          <DialogHeader>
            <DialogTitle>New Portfolio</DialogTitle>
          </DialogHeader>

          <div className="space-y-1">
            <label className="text-sm font-medium text-foreground">
              Portfolio name
            </label>
            <Input
              autoFocus
              placeholder="e.g. Tech Picks"
              value={name}
              maxLength={200}
              onChange={(e) => {
                setName(e.target.value)
                if (error) setError(null)
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !loading) handleSubmit()
              }}
              disabled={loading}
            />
            {error && (
              <p className="text-sm text-destructive mt-1">{error}</p>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={loading}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={loading}>
              {loading ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
