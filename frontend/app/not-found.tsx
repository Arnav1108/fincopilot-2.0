import Link from "next/link"

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background text-foreground">
      <p className="text-sm">This page doesn&apos;t exist.</p>
      <Link href="/chat" className="text-sm text-muted-foreground underline">
        Back to chat
      </Link>
    </div>
  )
}
