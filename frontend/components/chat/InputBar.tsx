"use client"

import { useCallback, useRef, useState } from "react"
import { ArrowUp, BarChart2, FileText, Loader2, Paperclip, X } from "lucide-react"
import { cn } from "@/lib/utils"
import type { IngestProgress } from "@/lib/types"

// ── constants ─────────────────────────────────────────────────────────────────

const LINE_HEIGHT = 24
const MAX_HEIGHT = LINE_HEIGHT * 5
const MAX_FILE_BYTES = 100 * 1024 * 1024 // 100 MB
const ALLOWED_EXTS = new Set(["pdf", "docx", "csv", "txt", "html"])

// ── file helpers ──────────────────────────────────────────────────────────────

function extOf(filename: string): string {
  return filename.split(".").pop()?.toLowerCase() ?? ""
}

function fileIcon(filename: string): string {
  switch (extOf(filename)) {
    case "pdf":  return "📄"
    case "csv":  return "📊"
    case "docx": return "📝"
    case "txt":  return "📋"
    case "html": return "🌐"
    default:     return "📎"
  }
}

function fileKey(f: File): string {
  return `${f.name}:${f.size}:${f.lastModified}`
}

// ── types ─────────────────────────────────────────────────────────────────────

interface Props {
  onSend: (message: string, files: File[]) => void
  disabled: boolean
  /** Live ingestion progress passed down while the SSE stream is in Phase 1. */
  ingestProgress?: IngestProgress | null
  /** Non-empty string is shown as an error banner above the input. */
  streamError?: string | null
}

// ── component ─────────────────────────────────────────────────────────────────

export default function InputBar({
  onSend,
  disabled,
  ingestProgress,
  streamError,
}: Props) {
  const [value, setValue] = useState("")
  const [files, setFiles] = useState<File[]>([])
  const [fileError, setFileError] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const isIngesting = Boolean(ingestProgress)
  const canSend = !disabled && value.trim().length > 0

  // ── file validation & collection ────────────────────────────────────────────

  const addFiles = useCallback((incoming: FileList | File[]) => {
    setFileError(null)
    const arr = Array.from(incoming)
    const errors: string[] = []
    const valid: File[] = []

    for (const file of arr) {
      const ext = extOf(file.name)
      if (!ALLOWED_EXTS.has(ext)) {
        errors.push(`"${file.name}" — unsupported type (PDF, DOCX, CSV, TXT, HTML only)`)
        continue
      }
      if (file.size > MAX_FILE_BYTES) {
        errors.push(`"${file.name}" — exceeds 100 MB limit`)
        continue
      }
      valid.push(file)
    }

    if (errors.length) setFileError(errors.join(" · "))

    if (valid.length) {
      setFiles((prev) => {
        const existing = new Set(prev.map(fileKey))
        return [...prev, ...valid.filter((f) => !existing.has(fileKey(f)))]
      })
    }
  }, [])

  const removeFile = useCallback((index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index))
    setFileError(null)
  }, [])

  // ── textarea ─────────────────────────────────────────────────────────────────

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value)
    const el = e.target
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`
  }

  // ── submit ────────────────────────────────────────────────────────────────────

  const submit = useCallback(() => {
    if (!canSend) return
    onSend(value.trim(), files)
    setValue("")
    setFiles([])
    setFileError(null)
    if (textareaRef.current) textareaRef.current.style.height = "auto"
  }, [canSend, onSend, value, files])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  // ── drag-drop ─────────────────────────────────────────────────────────────────

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    if (!disabled) setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    // Only clear when leaving the container itself, not a child element
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setIsDragging(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    if (!disabled && e.dataTransfer.files.length) {
      addFiles(e.dataTransfer.files)
    }
  }

  // ── ingestion progress label ──────────────────────────────────────────────────

  const ingestLabel = ingestProgress
    ? `Ingesting ${ingestProgress.ready}/${ingestProgress.total} document${ingestProgress.total !== 1 ? "s" : ""}…`
    : null

  // ── render ────────────────────────────────────────────────────────────────────

  return (
    <div className="px-4 pb-6 pt-2">
      {/* Stream error banner — shown when the SSE stream yields an error event */}
      {streamError && (
        <div
          role="alert"
          className="max-w-3xl mx-auto mb-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive"
        >
          {streamError}
        </div>
      )}

      {/* Client-side file validation error */}
      {fileError && (
        <div
          role="alert"
          className="max-w-3xl mx-auto mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-400"
        >
          {fileError}
        </div>
      )}

      {/* Main input container — also the drag-drop target */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={cn(
          "max-w-3xl mx-auto rounded-2xl border border-border bg-card shadow-lg transition-all",
          isDragging && "border-primary bg-primary/5 ring-2 ring-primary/20",
        )}
      >
        {/* Ingestion progress — visible while Phase 1 of the SSE stream runs */}
        {isIngesting && ingestProgress && (
          <div className="flex items-center gap-2 px-4 pb-1 pt-3 text-xs text-muted-foreground">
            <Loader2 size={12} className="animate-spin flex-shrink-0" />
            <span>
              {ingestLabel}
              {ingestProgress.failed > 0 && (
                <span className="ml-1 text-destructive">
                  ({ingestProgress.failed} failed)
                </span>
              )}
            </span>
          </div>
        )}

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={isDragging ? "Drop files here…" : "Ask me anything…"}
          rows={1}
          style={{ height: "auto", resize: "none" }}
          className="w-full bg-transparent px-4 pb-1 pt-3 text-sm text-foreground outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
        />

        {/* File chips */}
        {files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-4 pb-1 pt-1">
            {files.map((f, i) => (
              <span
                key={fileKey(f)}
                className="inline-flex max-w-[200px] items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-xs text-foreground"
              >
                <span aria-hidden>{fileIcon(f.name)}</span>
                <span className="truncate">{f.name}</span>
                <button
                  type="button"
                  onClick={() => removeFile(i)}
                  className="ml-0.5 flex-shrink-0 text-muted-foreground hover:text-foreground focus:outline-none"
                  aria-label={`Remove ${f.name}`}
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}

        {/* Bottom toolbar */}
        <div className="flex items-center justify-between px-3 pb-3 pt-1">
          <div className="flex items-center gap-1">
            {/* Hidden native file input */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.docx,.csv,.txt,.html"
              className="hidden"
              aria-hidden
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files)
                // Reset so the same file can be re-added after removal
                e.target.value = ""
              }}
            />

            {/* Paperclip — now enabled */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled}
              className={cn(
                "rounded-md p-1.5 transition-colors",
                disabled
                  ? "cursor-not-allowed text-muted-foreground opacity-50"
                  : files.length > 0
                    ? "text-primary hover:bg-primary/10"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              aria-label={`Attach file${files.length ? ` (${files.length} attached)` : ""}`}
            >
              <Paperclip size={16} />
            </button>

            {/* Placeholder buttons — still disabled */}
            <button
              disabled
              className="cursor-not-allowed rounded-md p-1.5 text-muted-foreground opacity-50"
              aria-label="SEC filing (coming soon)"
            >
              <FileText size={16} />
            </button>
            <button
              disabled
              className="cursor-not-allowed rounded-md p-1.5 text-muted-foreground opacity-50"
              aria-label="Peer comparison (coming soon)"
            >
              <BarChart2 size={16} />
            </button>
          </div>

          {/* Send button */}
          <button
            type="button"
            onClick={submit}
            disabled={!canSend}
            className={cn(
              "rounded-full p-1.5 transition-colors",
              canSend
                ? "bg-foreground text-background hover:bg-foreground/90"
                : "cursor-not-allowed text-muted-foreground opacity-50",
            )}
            aria-label="Send message"
          >
            <ArrowUp size={16} />
          </button>
        </div>
      </div>

      {/* Drag-drop hint — only visible while dragging */}
      {isDragging && (
        <p className="max-w-3xl mx-auto mt-1 text-center text-xs text-primary">
          Drop to attach · PDF, DOCX, CSV, TXT, HTML · max 100 MB each
        </p>
      )}
    </div>
  )
}
