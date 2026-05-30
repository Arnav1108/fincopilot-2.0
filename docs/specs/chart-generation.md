# Spec: Chart Generation

## Goal
Render Recharts visualisations below assistant text responses when the underlying tool data is chartable, so financial analysts can see patterns (trends, comparisons, allocations) instead of reading tables of numbers.

## Background
Today the synthesizer streams a markdown text answer and the frontend renders it. Tool results from `financial_data`, `company_comparator`, and `portfolio_analysis` often contain time-series, multi-company comparisons, or portfolio allocations that are far easier to read as a chart. The synthesizer already receives the full `tool_results` dict; the SSE stream already carries typed named events; Recharts is already installed in the frontend. What is missing is (1) the extraction step that decides whether data is chartable and converts it to a canonical payload, and (2) a frontend chart component wired into the SSE/persistence pipeline.

## Scope

### In scope
- A second GPT-4o call (JSON mode) in `synthesizer_node` after the text stream completes, which decides chart type and extracts the canonical chart payload from `tool_results`.
- A new `chart_data` SSE event emitted from `_stream_events` in `chat.py` between the last `token` event and the `done` event.
- A `chart_data` JSONB column on the `messages` table so charts persist on conversation reload.
- The `chart_data` field returned in the `MessageRead` API response and frontend type.
- A new `ChartBlock` React component that renders line, bar, and pie charts via Recharts.
- `MessageBubble` renders `<ChartBlock>` below the markdown text when `chart_data` is present.
- `MessageList` passes `streamingChartData` to the streaming bubble.
- `useStream` dispatches `onChartData` and the `chat/[id]/page` wires it up.
- Alembic migration `0009_add_chart_data_to_messages.py`.
- `chart_data` field on `AgentState`.

### Out of scope
- Custom chart interactions (zoom, pan, brushing). Recharts default tooltips and legends are acceptable.
- Chart export or download.
- User choosing or overriding chart type.
- Streaming chart data incrementally — the `chart_data` event is emitted once after the text stream.
- Multiple charts per message.
- Charts for RAG / document retrieval responses (only tool-result paths produce charts).
- New tools, new API endpoints, new DB tables beyond the single `chart_data` column.

## User flow

### Happy path — chartable data
1. User sends "Show me Apple's revenue for the last 4 quarters."
2. Backend: planner routes to `financial_data` tool, executor fetches quarterly revenue, synthesizer streams text answer token by token.
3. After the text stream completes, synthesizer calls GPT-4o in JSON mode. GPT-4o determines the data is chartable, returns a bar chart payload.
4. `synthesizer_node` puts `chart_data` on `AgentState`.
5. `_stream_events` emits `event: chart_data` with the payload, then emits `event: done`.
6. Frontend `useStream` fires `onChartData(payload)` → sets `streamingChartData`.
7. The streaming `MessageBubble` receives `streamingChartData` and renders `<ChartBlock>` below the text.
8. `done` fires → `onDone` calls `load(id)` → messages reload from DB → persisted message has `chart_data` populated → settled `MessageBubble` renders the same chart from `message.chart_data`.
9. User reloads the page → `listMessages` returns `chart_data` → chart renders again.

### Happy path — non-chartable data
1. User sends "What is Apple's current PE ratio?"
2. Synthesizer streams text answer. Second GPT-4o call returns `{"chart_type": null}`.
3. `synthesizer_node` returns `chart_data: None`.
4. No `chart_data` SSE event is emitted.
5. Frontend renders text only; `message.chart_data` is `null` on the persisted record.

### Chart extraction failure
1. Second GPT-4o call throws an exception or returns unparseable JSON.
2. `synthesizer_node` catches the exception, logs a warning at `WARNING` level with the error, and returns `chart_data: None`.
3. No `chart_data` SSE event is emitted. Text response is unaffected.

### RAG path (documents)
1. Synthesizer uses `_SYSTEM_PROMPT_RAG` — `successful` tool results are empty.
2. Chart extraction is skipped entirely; no second GPT-4o call is made.

## Detailed requirements

### Backend

1. `AgentState` in `state.py` gains a new key `chart_data: dict | None` (JSON-serialisable, per the existing serialisation constraint).
2. In `synthesizer_node`, chart extraction runs only when `successful` is non-empty (i.e., tool-result paths only). RAG and LLM-only paths skip extraction completely.
3. The chart extraction call uses the FULL, non-truncated tool result data (`json.dumps(envelope["data"])` with no length cap). The 500-char truncation that exists for the text synthesis call must NOT be applied to the extraction call.
4. The chart extraction call uses `response_format={"type": "json_object"}` (JSON mode). Model must be the same as `model` from state (default `settings.SYNTHESIZER_MODEL`).
5. The extraction system prompt instructs GPT-4o: if data is chartable return `{chart_type, title, x_axis_label, y_axis_label, series}`; if not chartable return `{"chart_type": null}`.
6. Chart type selection rules encoded in the prompt:
   - `"line"` for time-series data (prices over time, earnings by quarter ordered chronologically).
   - `"bar"` for categorical comparisons (quarterly revenue, multi-company metric comparison, ranked items).
   - `"pie"` for part-of-whole allocations (portfolio holdings by value, sector breakdown).
   - `null` for single scalar values, non-numeric results, or anything that does not form a meaningful 2D data set.
7. On successful extraction where `chart_type` is not null, `synthesizer_node` validates the response has the required fields (`chart_type`, `title`, `x_axis_label`, `y_axis_label`, `series`); if any field is missing, treat it as extraction failure (log warning, set `chart_data: None`).
8. On any exception during extraction, log `chart_extraction_failed` at WARNING level with `error=str(e)`, set `chart_data: None`, and continue. Never propagate the exception.
9. `synthesizer_node` returns `{"final_output": final_output, "chart_data": chart_data}` (chart_data is `None` or the validated dict).
10. In `chat.py` `_stream_events`, after `final_state = await task` and before the `done` event: if `final_state.get("chart_data")` is not None, emit `_sse("chart_data", final_state["chart_data"])`.
11. When persisting the assistant `Message` in Phase 4 of `_stream_events`, set `chart_data=final_state.get("chart_data")` on the `Message` ORM object.
12. The `Message` model in `conversation.py` gains `chart_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)`.
13. The `MessageRead` Pydantic schema in `schemas/conversation.py` gains `chart_data: Optional[dict] = None` so the field is returned by `GET /{conversation_id}/messages`.
14. Alembic migration `0009_add_chart_data_to_messages.py` adds `chart_data JSONB NULL` to `messages`. No index is needed (not queried by this field).

### Frontend

15. `lib/types.ts` defines a new `ChartDataPoint` interface: `{ x: string | number; y: number }`.
16. `lib/types.ts` defines a new `ChartSeries` interface: `{ name: string; data: ChartDataPoint[] }`.
17. `lib/types.ts` defines a new `ChartData` interface: `{ chart_type: "line" | "bar" | "pie"; title: string; x_axis_label: string; y_axis_label: string; series: ChartSeries[] }`.
18. `MessageRead` in `lib/types.ts` gains `chart_data?: ChartData | null`.
19. `SseEvent` union in `lib/types.ts` gains `{ event: "chart_data"; data: ChartData }`.
20. `UseStreamOptions` in `useStream.ts` gains an optional callback `onChartData?: (chartData: ChartData) => void`.
21. `useStream.ts` dispatches `onChartData?.(data as ChartData)` for the `"chart_data"` SSE event in the `switch` block.
22. The `onChartData` callback is included in the `useCallback` dependency array of `startStream`.
23. A new component `components/chat/ChartBlock.tsx` accepts `{ data: ChartData }` and renders:
    - `"line"` → Recharts `<LineChart>` with `<XAxis>`, `<YAxis>`, `<Tooltip>`, `<Legend>`, one `<Line>` per series.
    - `"bar"` → Recharts `<BarChart>` with `<XAxis>`, `<YAxis>`, `<Tooltip>`, `<Legend>`, one `<Bar>` per series.
    - `"pie"` → Recharts `<PieChart>` with `<Pie>`, `<Tooltip>`, `<Legend>`. For pie charts, each series item becomes one pie segment; `x` is used as the pie label and `y` as the value.
    - All charts render inside a `<ResponsiveContainer width="100%" height={300}>`.
    - A title (`<p>` with `font-semibold text-sm`) is rendered above the chart.
    - X-axis and Y-axis labels are set via `<XAxis label>` and `<YAxis label>` props where applicable (not applicable to pie).
24. `MessageBubble.tsx` renders `<ChartBlock data={message.chart_data} />` below the markdown text block when `!isUser && message.chart_data != null`.
25. `MessageBubble.tsx` also renders the chart during streaming: accepts a new optional prop `streamingChartData?: ChartData | null` and renders `<ChartBlock>` below the text when `isStreaming && streamingChartData != null`.
26. `MessageList.tsx` accepts a new optional prop `streamingChartData?: ChartData | null` and passes it to the streaming `MessageBubble`.
27. `app/chat/[id]/page.tsx` adds `const [streamingChartData, setStreamingChartData] = useState<ChartData | null>(null)`.
28. `app/chat/[id]/page.tsx` wires `onChartData: useCallback((cd) => setStreamingChartData(cd), [])` in the `useStream` options.
29. `app/chat/[id]/page.tsx` clears `streamingChartData` to `null` in `onDone` and `onError` callbacks.
30. `app/chat/[id]/page.tsx` clears `streamingChartData` to `null` in `handleSend` alongside the other state resets.
31. `MessageList` receives `streamingChartData={streamingChartData}` from the page and passes it to the streaming bubble.

## Data model changes

### `messages` table — add column

```sql
ALTER TABLE messages ADD COLUMN chart_data JSONB NULL;
```

- Column: `chart_data`, type `JSONB`, nullable, no default.
- No index: the column is only read by primary key (`message_id`) via the `listMessages` query; a JSONB index would provide no benefit.
- No foreign keys.

### Migration order
`0009_add_chart_data_to_messages.py` runs after `0008_portfolio_tables.py`.
Down migration: `ALTER TABLE messages DROP COLUMN chart_data`.

### `chart_data` JSON schema stored in the column

```json
{
  "chart_type": "line" | "bar" | "pie",
  "title": "string",
  "x_axis_label": "string",
  "y_axis_label": "string",
  "series": [
    {
      "name": "string",
      "data": [{ "x": "string or number", "y": "number" }]
    }
  ]
}
```

NULL in the column means the message had no chartable data (including all user messages, all RAG responses, and tool responses where GPT-4o determined no chart was appropriate).

## API contracts

### `GET /api/v1/conversations/{conversation_id}/messages`

No changes to method, path, auth, or request. The response schema for each `MessageRead` item gains one new optional field:

```
chart_data: object | null
  chart_type: "line" | "bar" | "pie"
  title: string
  x_axis_label: string
  y_axis_label: string
  series: [{ name: string, data: [{ x: string|number, y: number }] }]
```

Absent (null) for all user messages and assistant messages without charts.

### SSE stream — new event

Emitted from `POST /api/v1/conversations/{conversation_id}/stream` between the last `token` event and the `done` event, only when chartable data was detected.

```
event: chart_data
data: {"chart_type": "line"|"bar"|"pie", "title": "...", "x_axis_label": "...", "y_axis_label": "...", "series": [...]}
```

- Emitted at most once per request.
- Never emitted when chart extraction is skipped or fails.
- Never emitted for RAG paths.

## Component and file structure

### Backend — modified
- `backend/app/agent/state.py` — add `chart_data: dict | None` to `AgentState`.
- `backend/app/agent/synthesizer.py` — add `_extract_chart_data()` async helper and call it after the text stream; return `chart_data` in the state dict.
- `backend/app/api/v1/chat.py` — emit `chart_data` SSE event before `done`; persist `chart_data` on the `Message` record.
- `backend/app/models/conversation.py` — add `chart_data` mapped column.
- `backend/app/schemas/conversation.py` — add `chart_data` field to `MessageRead`.

### Backend — new
- `backend/alembic/versions/0009_add_chart_data_to_messages.py` — single-column migration.

### Frontend — modified
- `frontend/lib/types.ts` — add `ChartData`, `ChartSeries`, `ChartDataPoint` interfaces; update `MessageRead` and `SseEvent`.
- `frontend/hooks/useStream.ts` — add `onChartData` option and dispatch.
- `frontend/components/chat/MessageBubble.tsx` — render `<ChartBlock>` for settled and streaming messages.
- `frontend/components/chat/MessageList.tsx` — accept and forward `streamingChartData` prop.
- `frontend/app/chat/[id]/page.tsx` — add `streamingChartData` state and `onChartData` handler.

### Frontend — new
- `frontend/components/chat/ChartBlock.tsx` — Recharts wrapper for line/bar/pie charts.

### Tests — new
- `backend/tests/agent/test_synthesizer_chart.py` — unit tests for `_extract_chart_data`.
- `frontend/__tests__/ChartBlock.test.tsx` — snapshot/render tests for each chart type.

## External dependencies

### Recharts (already installed)
- Used for all three chart types via `LineChart`, `BarChart`, `PieChart`.
- If Recharts is unavailable (e.g., import error), the `ChartBlock` component should not be rendered and the `MessageBubble` should fall back to text-only. This is handled naturally by Next.js build failures surfacing at build time rather than runtime.

### OpenAI API (second call)
- One additional `chat.completions.create` call with `response_format={"type": "json_object"}` per chartable tool-result response.
- Latency: typically 1–3 seconds; runs after the text stream completes so the user sees text immediately.
- If the OpenAI API is unavailable or rate-limited, the exception is caught, a warning is logged, and `chart_data` is set to `None`. The text response is always delivered first, so this failure is invisible to the user.
- Rate limits: one extra call per chat turn with tool results. No batch calls; no separate quota needed.

## Testing plan

### Unit tests

`backend/tests/agent/test_synthesizer_chart.py`:
- `test_extract_chart_data_line`: mock OpenAI JSON response with `chart_type="line"`, assert returned dict matches expected shape.
- `test_extract_chart_data_null`: mock OpenAI JSON response with `{"chart_type": null}`, assert function returns `None`.
- `test_extract_chart_data_missing_field`: mock response missing `series`, assert function returns `None` and logs warning.
- `test_extract_chart_data_invalid_json`: mock OpenAI to raise an exception, assert function returns `None` and logs warning.
- `test_synthesizer_node_skips_chart_for_rag`: call `synthesizer_node` with `reranked_chunks` populated and `tool_results` empty; assert `chart_data` is `None` in returned dict and no second OpenAI call is made.
- `test_synthesizer_node_skips_chart_for_llm_only`: no chunks, no tool results; same assertion.

`frontend/__tests__/ChartBlock.test.tsx`:
- Renders a `<LineChart>` given `chart_type="line"` data without throwing.
- Renders a `<BarChart>` given `chart_type="bar"` data without throwing.
- Renders a `<PieChart>` given `chart_type="pie"` data without throwing.
- Renders the `title` text in all three cases.

### Integration tests

Extend `backend/tests/test_portfolio_api.py` or add `backend/tests/api/test_chart_persistence.py`:
- POST a message that triggers tool results → assert `chart_data` SSE event is present in the stream.
- GET messages for that conversation → assert `chart_data` field is non-null on the assistant message.
- Simulate chart extraction failure (mock OpenAI to raise) → assert `chart_data` SSE event is NOT present and `done` event IS present.

### Manual verification

1. Ask "Show me Apple's revenue for the last 4 quarters" → confirm bar chart renders below text response.
2. Ask "How's my portfolio doing?" → confirm pie chart renders below text response.
3. Ask "What is the current PE ratio of Apple?" → confirm no chart renders; text-only response.
4. Reload the page after step 1 → confirm chart is still visible (persistence check).
5. Trigger chart extraction failure (temporarily break the extraction prompt) → confirm text response still arrives, no chart, no user-visible error.
6. Send a message with an uploaded document (RAG path) → confirm no chart renders.

## Observability

### Logs (structlog)

| Event key | Level | Fields | When |
|---|---|---|---|
| `chart_extraction_started` | DEBUG | `model`, `tool_count` | Before second GPT-4o call |
| `chart_extraction_completed` | DEBUG | `chart_type`, `series_count` | Successful extraction with non-null chart |
| `chart_extraction_skipped` | DEBUG | `reason` (`"no_tool_results"` or `"chart_type_null"`) | GPT-4o returns null or no tool data |
| `chart_extraction_failed` | WARNING | `error` | Any exception during extraction |

Existing `synthesizer_completed` log gains two new fields: `chart_type` (str or None) and `chart_extraction_ms` (int, milliseconds for the second call).

### Healthy vs unhealthy

- Healthy: `chart_extraction_completed` or `chart_extraction_skipped` logged on every tool-result synthesizer call.
- Unhealthy signal: elevated rate of `chart_extraction_failed` in logs (monitor via structlog sink). A single failure is expected during OpenAI hiccups; sustained failures (>5% of tool-result calls) indicate a prompt or API issue.

## Risks and open questions

1. **Tool result data shape varies by tool.** The `financial_data`, `company_comparator`, and `portfolio_analysis` tools each return different JSON structures. GPT-4o must infer the chartable fields from whatever shape is present. If a tool returns an unexpected schema, GPT-4o may return `chart_type: null` rather than failing — this is acceptable behaviour. Consider including a few concrete examples in the extraction system prompt to improve accuracy.

2. **Second OpenAI call adds latency.** The extraction call runs after the text stream ends so users already see the text. The chart appears a second or two later. If this feels jarring, a loading skeleton in `ChartBlock` while `streamingChartData` is null could smooth the transition — deferred to a follow-up.

3. **Token cost.** Each tool-result response now consumes an extra ~500–2000 input tokens (tool data) plus ~200 output tokens. For high-frequency usage this adds up. If cost becomes a concern, the extraction prompt can be trimmed or a smaller model (e.g., `gpt-4o-mini`) can be used for extraction. The model is currently hardcoded to the same model as the synthesizer; extracting it to a `CHART_EXTRACTION_MODEL` setting would allow independent tuning.

4. **Multi-series bar charts with many data points.** Recharts bar charts with >10 x-axis labels can get crowded. The extraction prompt should be instructed to cap series at a reasonable number of points (e.g., 20). Not enforced in code — if GPT-4o over-extracts, the chart will render but may be hard to read.

5. **`chart_data` on the `AgentState`.** The existing serialisation constraint says all AgentState values must be JSON-serialisable. `dict | None` satisfies this, but implementers must ensure the chart dict coming out of GPT-4o is not passed through `json.loads` → Pydantic → back to dict in a way that introduces non-serialisable types. Plain `json.loads` of the GPT-4o response is the correct path.

6. **`chart_data` not included in the synthesizer's 500-char tool-result truncation for the text call.** The text synthesis call already truncates tool data at 500 chars. The chart extraction call must not use this truncated string — it must call `json.dumps(envelope["data"])` on the original tool result dict without any length cap. Implementers must be careful not to reuse the truncated `parts` variable.
