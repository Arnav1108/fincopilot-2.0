"use client"

import { useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"
import TagInput from "./TagInput"
import { getProfile, updateProfile } from "@/lib/api"
import type { InvestmentStyle, OutputFormat } from "@/lib/types"

const MAX_CONTEXT = 500
const TICKER_RE = /^[A-Z]{1,10}$/

interface Props {
  open: boolean
  onClose: () => void
}

interface FormState {
  sectors: string[]
  tracked_tickers: string[]
  investment_style: InvestmentStyle | null
  preferred_output_format: OutputFormat | null
  custom_context: string
}

const DEFAULT_FORM: FormState = {
  sectors: [],
  tracked_tickers: [],
  investment_style: null,
  preferred_output_format: null,
  custom_context: "",
}

export default function SettingsModal({ open, onClose }: Props) {
  const { getToken } = useAuth()
  const [form, setForm] = useState<FormState>(DEFAULT_FORM)
  const [isFetching, setIsFetching] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    setIsFetching(true)
    ;(async () => {
      try {
        const token = await getToken()
        if (!token) return
        const profile = await getProfile(token)
        setForm({
          sectors: profile.sectors ?? [],
          tracked_tickers: profile.tracked_tickers ?? [],
          investment_style: profile.investment_style,
          preferred_output_format: profile.preferred_output_format,
          custom_context: profile.custom_context ?? "",
        })
      } catch {
        setError("Failed to load profile.")
      } finally {
        setIsFetching(false)
      }
    })()
  }, [open, getToken])

  const handleSave = async () => {
    setIsSaving(true)
    setError(null)
    try {
      const token = await getToken()
      if (!token) return
      await updateProfile(token, {
        sectors: form.sectors,
        tracked_tickers: form.tracked_tickers,
        investment_style: form.investment_style,
        preferred_output_format: form.preferred_output_format,
        custom_context: form.custom_context || null,
      })
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save profile.")
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <DialogContent className="sm:max-w-lg bg-card border-border max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Analyst Profile</DialogTitle>
        </DialogHeader>

        {isFetching ? (
          <p className="text-sm text-muted-foreground py-6 text-center">Loading…</p>
        ) : (
          <div className="space-y-5 mt-1">
            {/* Sectors */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">Sectors</label>
              <TagInput
                value={form.sectors}
                onChange={(tags) => setForm((f) => ({ ...f, sectors: tags }))}
                placeholder="Add a sector and press Enter"
                maxTags={50}
              />
            </div>

            {/* Tracked Tickers */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">Tracked Tickers</label>
              <TagInput
                value={form.tracked_tickers}
                onChange={(tags) => setForm((f) => ({ ...f, tracked_tickers: tags }))}
                transform={(s) => s.toUpperCase()}
                validate={(s) => TICKER_RE.test(s)}
                placeholder="AAPL, MSFT… press Enter"
              />
            </div>

            {/* Investment Style */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">Investment Style</label>
              <Select
                value={form.investment_style ?? undefined}
                onValueChange={(v) =>
                  setForm((f) => ({ ...f, investment_style: v as InvestmentStyle }))
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select style" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="growth">Growth</SelectItem>
                  <SelectItem value="value">Value</SelectItem>
                  <SelectItem value="blend">Blend</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Preferred Output Format */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">
                Preferred Output Format
              </label>
              <Select
                value={form.preferred_output_format ?? undefined}
                onValueChange={(v) =>
                  setForm((f) => ({ ...f, preferred_output_format: v as OutputFormat }))
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select format" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="concise">Concise</SelectItem>
                  <SelectItem value="detailed">Detailed</SelectItem>
                  <SelectItem value="bullet_points">Bullet Points</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Custom Context */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium text-foreground">Custom Context</label>
                <span
                  className={cn(
                    "text-xs",
                    form.custom_context.length > MAX_CONTEXT
                      ? "text-destructive"
                      : "text-muted-foreground",
                  )}
                >
                  {form.custom_context.length}/{MAX_CONTEXT}
                </span>
              </div>
              <textarea
                value={form.custom_context}
                onChange={(e) => {
                  if (e.target.value.length <= MAX_CONTEXT) {
                    setForm((f) => ({ ...f, custom_context: e.target.value }))
                  }
                }}
                placeholder="Any additional context about your investment approach…"
                rows={3}
                style={{ resize: "none" }}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
              />
            </div>

            {error && <p className="text-sm text-destructive text-center">{error}</p>}

            {/* Save */}
            <div className="flex justify-end pt-1">
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {isSaving ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
