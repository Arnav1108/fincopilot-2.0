// Loading placeholder shaped like a real transcript: user pill on the right,
// assistant answer lines behind the accent rule on the left.
export default function TranscriptSkeleton() {
  return (
    <div className="flex-1 overflow-hidden" aria-hidden>
      <div className="mx-auto max-w-3xl animate-pulse space-y-8 px-4 pt-8">
        <div className="flex justify-end">
          <div className="h-10 w-2/5 rounded-3xl bg-muted/70" />
        </div>
        <div className="space-y-2.5 border-l-2 border-border pl-4">
          <div className="h-3.5 w-full rounded bg-muted/70" />
          <div className="h-3.5 w-11/12 rounded bg-muted/70" />
          <div className="h-3.5 w-4/5 rounded bg-muted/70" />
          <div className="h-3.5 w-2/3 rounded bg-muted/50" />
        </div>
        <div className="flex justify-end">
          <div className="h-10 w-1/3 rounded-3xl bg-muted/70" />
        </div>
        <div className="space-y-2.5 border-l-2 border-border pl-4">
          <div className="h-3.5 w-10/12 rounded bg-muted/70" />
          <div className="h-3.5 w-full rounded bg-muted/60" />
          <div className="h-3.5 w-1/2 rounded bg-muted/50" />
        </div>
      </div>
    </div>
  )
}
