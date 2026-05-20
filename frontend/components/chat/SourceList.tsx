import type { Source } from "@/lib/types"

interface Props {
  sources: Source[]
}

export default function SourceList({ sources }: Props) {
  return (
    <div className="mt-2 space-y-0.5">
      <p className="text-xs font-medium text-muted-foreground">Sources</p>
      {sources.map((s, i) => (
        <a
          key={i}
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block text-xs text-muted-foreground underline hover:text-foreground truncate"
        >
          {s.title}
        </a>
      ))}
    </div>
  )
}
