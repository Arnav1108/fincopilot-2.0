"use client"

import { useRef, useState } from "react"
import { ArrowUp, BarChart2, FileText, Paperclip } from "lucide-react"
import { cn } from "@/lib/utils"

const LINE_HEIGHT = 24
const MAX_HEIGHT = LINE_HEIGHT * 5

interface Props {
  onSend: (message: string) => void
  disabled: boolean
}

export default function InputBar({ onSend, disabled }: Props) {
  const [value, setValue] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const canSend = !disabled && value.trim().length > 0

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value)
    const el = e.target
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`
  }

  const submit = () => {
    if (!canSend) return
    onSend(value.trim())
    setValue("")
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="px-4 pb-6 pt-2">
      <div className="max-w-3xl mx-auto rounded-2xl border border-border bg-card shadow-lg">
        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder="Ask me anything…"
          rows={1}
          style={{ height: "auto", resize: "none" }}
          className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none disabled:cursor-not-allowed px-4 pt-3 pb-1"
        />

        {/* Bottom toolbar */}
        <div className="flex items-center justify-between px-3 pb-3 pt-1">
          <div className="flex items-center gap-1">
            <button
              disabled
              className="p-1.5 rounded-md text-muted-foreground cursor-not-allowed opacity-50"
              aria-label="Attach file"
            >
              <Paperclip size={16} />
            </button>
            <button
              disabled
              className="p-1.5 rounded-md text-muted-foreground cursor-not-allowed opacity-50"
              aria-label="SEC filing"
            >
              <FileText size={16} />
            </button>
            <button
              disabled
              className="p-1.5 rounded-md text-muted-foreground cursor-not-allowed opacity-50"
              aria-label="Peer comparison"
            >
              <BarChart2 size={16} />
            </button>
          </div>

          <button
            onClick={submit}
            disabled={!canSend}
            className={cn(
              "p-1.5 rounded-full transition-colors",
              canSend
                ? "bg-foreground text-background hover:bg-foreground/90"
                : "text-muted-foreground cursor-not-allowed opacity-50",
            )}
            aria-label="Send message"
          >
            <ArrowUp size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}
