"use client"

import { Component, type ReactNode } from "react"
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts"
import type { ChartData } from "@/lib/types"

const COLORS = ["#2563eb", "#16a34a", "#dc2626", "#d97706", "#7c3aed", "#0891b2"]

function formatPieValue(value: number, yAxisLabel: string): string {
  if (yAxisLabel?.includes("%")) {
    return `${value.toFixed(2)}%`
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

class ChartErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: ReactNode }) {
    super(props)
    this.state = { hasError: false }
  }
  static getDerivedStateFromError() {
    return { hasError: true }
  }
  render() {
    if (this.state.hasError) {
      return <p className="text-sm text-muted-foreground">Chart unavailable</p>
    }
    return this.props.children
  }
}

interface Props {
  data?: ChartData
  isLoading?: boolean
}

export default function ChartBlock({ data, isLoading }: Props) {
  if (isLoading) {
    return (
      <div className="mt-3 animate-pulse rounded-lg border border-border bg-muted/20 p-4">
        <div className="mb-3 h-4 w-40 rounded bg-muted" />
        <div className="min-h-[200px] rounded bg-muted" />
      </div>
    )
  }

  if (!data) return null

  // Pivot series format → flat record array for line/bar: [{x, seriesName: value, ...}]
  const flatData = (() => {
    const seen = new Map<string | number, Record<string, string | number>>()
    for (const s of data.series) {
      for (const pt of s.data) {
        if (!seen.has(pt.x)) seen.set(pt.x, { x: pt.x })
        seen.get(pt.x)![s.name] = pt.y
      }
    }
    return Array.from(seen.values())
  })()

  // Flatten all series data points for pie: [{name: x_label, value: y}]
  const pieData = data.series.flatMap((s) =>
    s.data.map((pt) => ({ name: String(pt.x), value: pt.y }))
  )

  return (
    <div className="mt-3 rounded-lg border border-border bg-muted/20 p-4">
      <p className="mb-3 text-sm font-semibold text-foreground">{data.title}</p>
      <ChartErrorBoundary>
        <ResponsiveContainer width="100%" height={300} className="min-h-[200px]">
          {data.chart_type === "pie" ? (
            <PieChart>
              <Pie
                data={pieData}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                outerRadius={100}
                label={({ name, value }: { name: string; value: number }) =>
                  `${name}: ${formatPieValue(value, data.y_axis_label)}`
                }
              >
                {pieData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value: number) => [
                  formatPieValue(value, data.y_axis_label),
                ]}
              />
              <Legend />
            </PieChart>
          ) : data.chart_type === "line" ? (
            <LineChart data={flatData} margin={{ bottom: 24, left: 10, right: 10 }}>
              <XAxis
                dataKey="x"
                tick={{ fontSize: 11 }}
                label={
                  data.x_axis_label
                    ? { value: data.x_axis_label, position: "insideBottom", offset: -12, fontSize: 11 }
                    : undefined
                }
              />
              <YAxis
                tick={{ fontSize: 11 }}
                label={
                  data.y_axis_label
                    ? { value: data.y_axis_label, angle: -90, position: "insideLeft", offset: 10, fontSize: 11 }
                    : undefined
                }
              />
              <Tooltip />
              <Legend />
              {data.series.map((s, i) => (
                <Line
                  key={s.name}
                  type="monotone"
                  dataKey={s.name}
                  stroke={COLORS[i % COLORS.length]}
                  dot={false}
                  strokeWidth={2}
                />
              ))}
            </LineChart>
          ) : (
            <BarChart data={flatData} margin={{ bottom: 24, left: 10, right: 10 }}>
              <XAxis
                dataKey="x"
                tick={{ fontSize: 11 }}
                label={
                  data.x_axis_label
                    ? { value: data.x_axis_label, position: "insideBottom", offset: -12, fontSize: 11 }
                    : undefined
                }
              />
              <YAxis
                tick={{ fontSize: 11 }}
                label={
                  data.y_axis_label
                    ? { value: data.y_axis_label, angle: -90, position: "insideLeft", offset: 10, fontSize: 11 }
                    : undefined
                }
              />
              <Tooltip />
              <Legend />
              {data.series.map((s, i) => (
                <Bar key={s.name} dataKey={s.name} fill={COLORS[i % COLORS.length]} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </ChartErrorBoundary>
    </div>
  )
}
