"use client"

import { cn } from "@/lib/utils"

interface Props {
  content: string
}

interface ParsedTable {
  before: string
  headers: string[]
  rows: string[][]
  after: string
}

function parseRow(line: string): string[] {
  const parts = line.split("|")
  return parts.slice(1, parts.length - 1).map((s) => s.trim())
}

function isSeparatorRow(line: string): boolean {
  return /^\s*\|[-\s:|]+\s*$/.test(line)
}

function parseMarkdownTable(content: string): ParsedTable | null {
  const lines = content.split("\n")
  let tableStart = -1
  let tableEnd = -1

  for (let i = 0; i < lines.length - 1; i++) {
    if (lines[i].trimStart().startsWith("|") && isSeparatorRow(lines[i + 1])) {
      tableStart = i
      break
    }
  }

  if (tableStart === -1) return null

  tableEnd = tableStart
  for (let i = tableStart; i < lines.length; i++) {
    if (lines[i].trimStart().startsWith("|")) tableEnd = i
    else break
  }

  const before = lines.slice(0, tableStart).join("\n").trim()
  const after = lines.slice(tableEnd + 1).join("\n").trim()
  const tableLines = lines.slice(tableStart, tableEnd + 1)

  const headers = parseRow(tableLines[0])
  const rows = tableLines
    .slice(2)
    .filter((l) => l.trimStart().startsWith("|"))
    .map(parseRow)

  if (headers.length === 0) return null

  return { before, headers, rows, after }
}

function cellClasses(value: string, colIndex: number): string {
  if (colIndex === 0) return "px-3 py-2 font-semibold text-left"

  const cleaned = value.replace(/[$,%\s]/g, "")
  const num = parseFloat(cleaned)
  if (!isNaN(num) && cleaned !== "") {
    const base = "px-3 py-2 text-right font-mono"
    if (num > 0) return cn(base, "text-green-600")
    if (num < 0) return cn(base, "text-red-500")
    return base
  }
  return "px-3 py-2 text-left"
}

export function hasMarkdownTable(content: string): boolean {
  const lines = content.split("\n")
  for (let i = 0; i < lines.length - 1; i++) {
    if (lines[i].trimStart().startsWith("|") && isSeparatorRow(lines[i + 1])) {
      return true
    }
  }
  return false
}

export default function ComparisonTable({ content }: Props) {
  const parsed = parseMarkdownTable(content)

  if (!parsed) {
    return <p className="text-sm leading-relaxed text-foreground">{content}</p>
  }

  const { before, headers, rows, after } = parsed

  return (
    <div className="flex flex-col gap-3">
      {before && (
        <p className="text-sm leading-relaxed text-foreground">{before}</p>
      )}

      <div className="w-full overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-muted">
              {headers.map((h, i) => (
                <th
                  key={i}
                  className={cn(
                    "px-3 py-2 font-semibold text-foreground text-left",
                    i > 0 && "text-right",
                  )}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr
                key={ri}
                className={cn(
                  "border-t border-border transition-colors hover:bg-muted/50",
                  ri % 2 === 0
                    ? "bg-white dark:bg-transparent"
                    : "bg-muted/[0.05]",
                )}
              >
                {row.map((cell, ci) => (
                  <td key={ci} className={cellClasses(cell, ci)}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {after && (
        <p className="text-sm leading-relaxed text-foreground">{after}</p>
      )}
    </div>
  )
}
