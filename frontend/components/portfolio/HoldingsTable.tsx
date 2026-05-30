"use client"

import { useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { deleteHolding } from "@/lib/api"
import { ApiError } from "@/lib/api"
import type { HoldingRead } from "@/lib/types"

interface Props {
  portfolioId: string
  holdings: HoldingRead[]
  onDeleted: (holdingId: string) => void
}

function formatDecimal(value: string): string {
  return parseFloat(value).toString()
}

export default function HoldingsTable({ portfolioId, holdings, onDeleted }: Props) {
  const { getToken } = useAuth()
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [rowError, setRowError] = useState<string | null>(null)

  const handleDelete = async (holdingId: string) => {
    const token = await getToken()
    if (!token) return
    setDeletingId(holdingId)
    setRowError(null)
    try {
      await deleteHolding(token, portfolioId, holdingId)
      setPendingDeleteId(null)
      setDeletingId(null)
      onDeleted(holdingId)
    } catch (e) {
      setPendingDeleteId(null)
      setDeletingId(null)
      if (e instanceof ApiError) {
        setRowError(e.message)
      } else {
        setRowError("Failed to delete holding. Please try again.")
      }
    }
  }

  if (holdings.length === 0) {
    return (
      <p className="text-xs text-muted-foreground text-center py-4">
        No holdings yet. Click Add Holding to get started.
      </p>
    )
  }

  return (
    <div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Ticker</TableHead>
            <TableHead>Shares</TableHead>
            <TableHead>Avg Cost Basis</TableHead>
            <TableHead className="w-[100px]" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {holdings.map((holding) => {
            const isPending = pendingDeleteId === holding.id
            const isDeleting = deletingId === holding.id
            return (
              <TableRow
                key={holding.id}
                className={isPending ? "bg-destructive/10" : undefined}
              >
                <TableCell className="font-medium">{holding.ticker}</TableCell>
                <TableCell>{formatDecimal(holding.shares)}</TableCell>
                <TableCell>
                  {holding.avg_cost_basis
                    ? formatDecimal(holding.avg_cost_basis)
                    : "—"}
                </TableCell>
                <TableCell className="text-right">
                  {isPending ? (
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={isDeleting}
                        onClick={() => handleDelete(holding.id)}
                      >
                        {isDeleting ? "Deleting…" : "Delete"}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setPendingDeleteId(null)}
                      >
                        Cancel
                      </Button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setRowError(null)
                        setPendingDeleteId(holding.id)
                      }}
                      className="text-muted-foreground hover:text-destructive transition-colors p-1"
                      aria-label={`Delete ${holding.ticker}`}
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
      {rowError && (
        <p className="text-xs text-destructive mt-2 px-1">{rowError}</p>
      )}
    </div>
  )
}
