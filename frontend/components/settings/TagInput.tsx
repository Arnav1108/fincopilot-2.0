"use client"

import { useRef, useState } from "react"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"

interface Props {
  value: string[]
  onChange: (tags: string[]) => void
  transform?: (s: string) => string
  validate?: (s: string) => boolean
  placeholder?: string
  maxTags?: number
}

export default function TagInput({
  value,
  onChange,
  transform,
  validate,
  placeholder,
  maxTags,
}: Props) {
  const [input, setInput] = useState("")
  const [invalid, setInvalid] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const addTag = (raw: string) => {
    const trimmed = raw.trim()
    if (!trimmed) return
    if (maxTags !== undefined && value.length >= maxTags) return
    const tag = transform ? transform(trimmed) : trimmed
    if (value.includes(tag)) {
      setInput("")
      return
    }
    if (validate && !validate(tag)) {
      setInvalid(true)
      setTimeout(() => setInvalid(false), 600)
      return
    }
    onChange([...value, tag])
    setInput("")
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      addTag(input)
    } else if (e.key === ",") {
      e.preventDefault()
      addTag(input)
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value
    if (v.endsWith(",")) {
      addTag(v.slice(0, -1))
    } else {
      setInput(v)
    }
  }

  const removeTag = (index: number) => {
    onChange(value.filter((_, i) => i !== index))
  }

  return (
    <div
      className={cn(
        "flex flex-wrap gap-1.5 min-h-10 w-full rounded-md border bg-background px-3 py-2 text-sm cursor-text focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-0",
        invalid ? "border-destructive" : "border-input",
      )}
      onClick={() => inputRef.current?.focus()}
    >
      {value.map((tag, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 bg-secondary text-secondary-foreground rounded-md px-2 py-0.5 text-xs"
        >
          {tag}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              removeTag(i)
            }}
            className="hover:text-foreground transition-colors"
            aria-label={`Remove ${tag}`}
          >
            <X size={10} />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        value={input}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={value.length === 0 ? placeholder : undefined}
        className="flex-1 min-w-[80px] bg-transparent outline-none placeholder:text-muted-foreground text-sm"
      />
    </div>
  )
}
