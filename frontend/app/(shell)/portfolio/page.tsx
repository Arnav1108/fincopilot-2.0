"use client"

import { useCallback, useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { Button } from "@/components/ui/button"
import AddHoldingDialog from "@/components/portfolio/AddHoldingDialog"
import CreatePortfolioDialog from "@/components/portfolio/CreatePortfolioDialog"
import DeletePortfolioDialog from "@/components/portfolio/DeletePortfolioDialog"
import HoldingsTable from "@/components/portfolio/HoldingsTable"
import { listPortfolios } from "@/lib/api"
import type { HoldingRead, PortfolioRead } from "@/lib/types"

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

interface PortfolioCardProps {
  portfolio: PortfolioRead
  onDeleted: () => void
  onHoldingAdded: (holding: HoldingRead) => void
  onHoldingDeleted: (holdingId: string) => void
}

function PortfolioCard({ portfolio, onDeleted, onHoldingAdded, onHoldingDeleted }: PortfolioCardProps) {
  return (
    <div className="rounded-lg border border-border bg-card">
      {/* Card header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-3 min-w-0">
          <span className="font-semibold text-foreground truncate">{portfolio.name}</span>
          <span className="text-xs text-muted-foreground shrink-0">
            {formatDate(portfolio.created_at)}
          </span>
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground shrink-0">
            {portfolio.holdings.length} holding{portfolio.holdings.length !== 1 ? "s" : ""}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <AddHoldingDialog portfolioId={portfolio.id} onAdded={onHoldingAdded} />
          <DeletePortfolioDialog portfolio={portfolio} onDeleted={onDeleted} />
        </div>
      </div>

      {/* Holdings table */}
      <div className="px-4 py-3">
        <HoldingsTable
          portfolioId={portfolio.id}
          holdings={portfolio.holdings}
          onDeleted={onHoldingDeleted}
        />
      </div>
    </div>
  )
}

export default function PortfolioPage() {
  const { getToken } = useAuth()
  const [portfolios, setPortfolios] = useState<PortfolioRead[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

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
      const data = await listPortfolios(token)
      if (!cancelled) setPortfolios(data)
    } catch (e) {
      if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load portfolios")
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

  const handlePortfolioCreated = (portfolio: PortfolioRead) => {
    setPortfolios((prev) => [portfolio, ...prev])
  }

  const handlePortfolioDeleted = (id: string) => {
    setPortfolios((prev) => prev.filter((p) => p.id !== id))
  }

  const handleHoldingAdded = (portfolioId: string, holding: HoldingRead) => {
    setPortfolios((prev) =>
      prev.map((p) =>
        p.id === portfolioId
          ? { ...p, holdings: [...p.holdings, holding] }
          : p,
      ),
    )
  }

  const handleHoldingDeleted = (portfolioId: string, holdingId: string) => {
    setPortfolios((prev) =>
      prev.map((p) =>
        p.id === portfolioId
          ? { ...p, holdings: p.holdings.filter((h) => h.id !== holdingId) }
          : p,
      ),
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-2xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-foreground">Portfolios</h1>
          <CreatePortfolioDialog onCreated={handlePortfolioCreated} />
        </div>

        {/* Loading */}
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading portfolios…</p>
        )}

        {/* Error */}
        {error && (
          <div className="flex items-center justify-between rounded-md border border-destructive/50 p-4 text-sm text-destructive">
            <span>{error}</span>
            <Button variant="outline" size="sm" onClick={load}>
              Retry
            </Button>
          </div>
        )}

        {/* Empty state */}
        {!isLoading && !error && portfolios.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-12">
            No portfolios yet. Create one to start tracking holdings.
          </p>
        )}

        {/* Portfolio list */}
        {portfolios.map((portfolio) => (
          <PortfolioCard
            key={portfolio.id}
            portfolio={portfolio}
            onDeleted={() => handlePortfolioDeleted(portfolio.id)}
            onHoldingAdded={(h) => handleHoldingAdded(portfolio.id, h)}
            onHoldingDeleted={(hId) => handleHoldingDeleted(portfolio.id, hId)}
          />
        ))}
      </div>
    </div>
  )
}
