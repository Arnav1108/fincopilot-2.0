export interface DocumentRead {
  id: string
  filename: string
  doc_type: string
  status: 'pending' | 'processing' | 'ready' | 'failed'
  chunk_count: number | null
  ticker: string | null
  created_at: string
}

export interface ConversationDocumentsResponse {
  documents: DocumentRead[]
}

export interface ConversationRead {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface MessageRead {
  id: string
  conversation_id: string
  role: "user" | "assistant"
  content: string
  created_at: string
  // RAG metadata — present on assistant messages after migration 0005
  rag_used?: boolean
  relevance_score?: number | null
  retrieved_chunk_ids?: string[] | null
  chart_data?: ChartData | null
}

export interface IngestProgress {
  total: number
  pending: number
  ready: number
  failed: number
}

export interface Source {
  title: string
  url: string
}

export interface ToolCall {
  tool_name: string
  status: "running" | "complete" | "ingesting"
  message?: string
}

export type InvestmentStyle = "growth" | "value" | "blend"
export type OutputFormat = "concise" | "detailed" | "bullet_points"

export interface ProfileRead {
  id: string
  user_id: string
  sectors: string[]
  tracked_tickers: string[]
  investment_style: InvestmentStyle | null
  preferred_output_format: OutputFormat | null
  custom_context: string | null
  created_at: string
  updated_at: string
}

export interface ProfileUpdate {
  sectors?: string[]
  tracked_tickers?: string[]
  investment_style?: InvestmentStyle | null
  preferred_output_format?: OutputFormat | null
  custom_context?: string | null
}

export interface ConfirmationRequired {
  token: string
  ticker: string
  filing_type: string
  period: string
  description: string
}

export interface ChartDataPoint {
  x: string | number
  y: number
}

export interface ChartSeries {
  name: string
  data: ChartDataPoint[]
}

export interface ChartData {
  chart_type: "line" | "bar" | "pie"
  title: string
  x_axis_label: string
  y_axis_label: string
  series: ChartSeries[]
}

export type SseEvent =
  | { event: "node_update"; data: { node: string; status: string } }
  | { event: "tool_call"; data: { tool: string; input: Record<string, unknown> } }
  | { event: "sources"; data: { sources: Source[] } }
  | { event: "token"; data: { token: string } }
  | { event: "done"; data: { message_id: string | null } }
  | { event: "ingest_progress"; data: IngestProgress }
  | { event: "ingest_complete"; data: { document_count: number } }
  | { event: "confirmation_required"; data: ConfirmationRequired }
  | { event: "confirmed"; data: { token: string; answer: string } }
  | { event: "chart_data"; data: ChartData }
  | { event: "error"; data: { code?: string; message: string; failed_files?: string[]; pending_ids?: string[] } }

export interface HoldingRead {
  id: string
  portfolio_id: string
  ticker: string
  shares: string
  avg_cost_basis: string | null
  created_at: string
  updated_at: string
}

export interface PortfolioRead {
  id: string
  user_id: string
  name: string
  created_at: string
  updated_at: string
  holdings: HoldingRead[]
}

export interface HoldingCreate {
  ticker: string
  shares: string
  avg_cost_basis?: string | null
}
