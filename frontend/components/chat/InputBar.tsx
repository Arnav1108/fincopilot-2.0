"use client"

import { useCallback, useLayoutEffect, useRef, useState } from "react"
import { ArrowUp, Loader2, Paperclip, Square, X } from "lucide-react"
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
  onStop?: () => void
  disabled: boolean
  /** Live ingestion progress passed down while the SSE stream is in Phase 1. */
  ingestProgress?: IngestProgress | null
  /** Non-empty string is shown as an error banner above the input. */
  streamError?: string | null
}

// ── component ─────────────────────────────────────────────────────────────────

export default function InputBar({
  onSend,
  onStop,
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
  // Mirrors `files` state but updated synchronously so submit() never reads a
  // stale closure value when React hasn't committed the batch yet (React 18
  // automatic batching defers state commits, causing a race with Playwright).
  const filesRef = useRef<File[]>([])

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
      const existing = new Set(filesRef.current.map(fileKey))
      const next = [...filesRef.current, ...valid.filter((f) => !existing.has(fileKey(f)))]
      filesRef.current = next  // synchronous — always current for submit()
      setFiles(next)           // async — drives the chip rendering
    }
  }, [])

  // Native event listener so Playwright's setInputFiles (which dispatches an
  // untrusted 'change' event that React's delegated handler may not catch)
  // still reaches addFiles directly at the element level.
  // useLayoutEffect runs synchronously after React commits the DOM (before
  // paint), ensuring the listener is registered before Playwright's CDP
  // setInputFiles fires the change event.
  useLayoutEffect(() => {
    const input = fileInputRef.current
    if (!input) return
    const onNativeChange = () => {
      if (input.files && input.files.length > 0) {
        addFiles(input.files)
      }
      // Defer reset so File objects remain valid until React commits the state.
      setTimeout(() => {
        if (fileInputRef.current) fileInputRef.current.value = ""
      }, 0)
    }
    input.addEventListener("change", onNativeChange)
    return () => input.removeEventListener("change", onNativeChange)
  }, [addFiles])

  const removeFile = useCallback((index: number) => {
    filesRef.current = filesRef.current.filter((_, i) => i !== index)
    setFiles(filesRef.current)
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
    // Read from ref so we never send a stale empty array when React's batch
    // hasn't committed the setFiles() call triggered by the native listener.
    const filesToSend = filesRef.current
    onSend(value.trim(), filesToSend)
    filesRef.current = []
    setValue("")
    setFiles([])
    setFileError(null)
    if (textareaRef.current) textareaRef.current.style.height = "auto"
  }, [canSend, onSend, value])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (disabled) return
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
          <div data-testid="ingest-progress" className="flex items-center gap-2 px-4 pb-1 pt-3 text-xs text-muted-foreground">
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
          data-testid="message-input"
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={isDragging ? "Drop files here…" : "Ask me anything…"}
          rows={1}
          style={{ height: "auto", resize: "none" }}
          className="w-full bg-transparent px-4 pb-1 pt-3 text-sm text-foreground outline-none placeholder:text-muted-foreground"
        />

        {/* File chips */}
        {files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-4 pb-1 pt-1">
            {files.map((f, i) => (
              <span
                key={fileKey(f)}
                data-testid="file-chip"
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
            {/* Native file input — sr-only (not display:none) so Playwright's
                setInputFiles can reach it. The native 'change' listener above
                handles addFiles + deferred reset; onChange is a React fallback. */}
            <input
              ref={fileInputRef}
              data-testid="file-input"
              type="file"
              multiple
              accept=".pdf,.docx,.csv,.txt,.html"
              className="sr-only"
              tabIndex={-1}
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files)
              }}
            />

            {/* Paperclip */}
            <button
              type="button"
              data-testid="attach-button"
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                "rounded-md p-1.5 transition-colors",
                files.length > 0
                  ? "text-primary hover:bg-primary/10"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              aria-label={`Attach file${files.length ? ` (${files.length} attached)` : ""}`}
            >
              <Paperclip size={16} />
            </button>


          </div>

          {/* Send / Stop button */}
          {disabled ? (
            <button
              type="button"
              onClick={onStop}
              className="rounded-full p-1.5 transition-colors bg-foreground text-background hover:bg-foreground/90"
              aria-label="Stop generating"
            >
              <Square size={14} fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              data-testid="send-button"
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
          )}
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
