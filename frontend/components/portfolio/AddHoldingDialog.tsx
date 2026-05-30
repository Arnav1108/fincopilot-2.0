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
import { ApiError, addHolding } from "@/lib/api"
import type { HoldingRead } from "@/lib/types"

interface Props {
  portfolioId: string
  onAdded: (holding: HoldingRead) => void
}

interface FieldErrors {
  ticker?: string
  shares?: string
  costBasis?: string
  api?: string
}

const TICKER_RE = /^[A-Z0-9]{1,10}$/

function parse422(body: string): string {
  try {
    const j = JSON.parse(body)
    if (Array.isArray(j.detail)) return j.detail[0].msg
    return String(j.detail)
  } catch {
    return body
  }
}

export default function AddHoldingDialog({ portfolioId, onAdded }: Props) {
  const { getToken } = useAuth()
  const [open, setOpen] = useState(false)
  const [ticker, setTicker] = useState("")
  const [shares, setShares] = useState("")
  const [costBasis, setCostBasis] = useState("")
  const [errors, setErrors] = useState<FieldErrors>({})
  const [loading, setLoading] = useState(false)

  const handleOpenChange = (value: boolean) => {
    if (!value) {
      setTicker("")
      setShares("")
      setCostBasis("")
      setErrors({})
      setLoading(false)
    }
    setOpen(value)
  }

  const validate = (): FieldErrors => {
    const errs: FieldErrors = {}
    if (!TICKER_RE.test(ticker)) {
      errs.ticker = "Ticker must be 1–10 uppercase letters or digits"
    }
    if (!shares || parseFloat(shares) <= 0) {
      errs.shares = "Shares must be greater than 0"
    }
    if (costBasis && parseFloat(costBasis) <= 0) {
      errs.costBasis = "Cost basis must be greater than 0"
    }
    return errs
  }

  const handleSubmit = async () => {
    const errs = validate()
    if (Object.keys(errs).length > 0) {
      setErrors(errs)
      return
    }
    const token = await getToken()
    if (!token) return
    setLoading(true)
    setErrors({})
    try {
      const holding = await addHolding(token, portfolioId, {
        ticker,
        shares,
        avg_cost_basis: costBasis || null,
      })
      setOpen(false)
      onAdded(holding)
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        setErrors({ api: parse422(e.message) })
      } else {
        setErrors({ api: "Something went wrong. Please try again." })
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Add Holding
      </Button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="sm:max-w-md bg-card border-border">
          <DialogHeader>
            <DialogTitle>Add Holding</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-1">
              <label className="text-sm font-medium text-foreground">Ticker</label>
              <Input
                autoFocus
                placeholder="AAPL"
                value={ticker}
                onChange={(e) => {
                  setTicker(e.target.value.toUpperCase())
                  if (errors.ticker) setErrors((prev) => ({ ...prev, ticker: undefined }))
                }}
                disabled={loading}
              />
              {errors.ticker && (
                <p className="text-sm text-destructive mt-1">{errors.ticker}</p>
              )}
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium text-foreground">Shares</label>
              <Input
                type="number"
                min="0"
                step="any"
                placeholder="10"
                value={shares}
                onChange={(e) => {
                  setShares(e.target.value)
                  if (errors.shares) setErrors((prev) => ({ ...prev, shares: undefined }))
                }}
                disabled={loading}
              />
              {errors.shares && (
                <p className="text-sm text-destructive mt-1">{errors.shares}</p>
              )}
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium text-foreground">
                Avg Cost Basis <span className="text-muted-foreground font-normal">(optional)</span>
              </label>
              <Input
                type="number"
                min="0"
                step="any"
                placeholder="150.00"
                value={costBasis}
                onChange={(e) => {
                  setCostBasis(e.target.value)
                  if (errors.costBasis) setErrors((prev) => ({ ...prev, costBasis: undefined }))
                }}
                disabled={loading}
              />
              {errors.costBasis && (
                <p className="text-sm text-destructive mt-1">{errors.costBasis}</p>
              )}
            </div>

            {errors.api && (
              <p className="text-sm text-destructive">{errors.api}</p>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={loading}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={loading}>
              {loading ? "Adding…" : "Add Holding"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
