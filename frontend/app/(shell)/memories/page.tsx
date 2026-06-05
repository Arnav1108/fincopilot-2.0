// Memories page disabled. To restore: git checkout HEAD -- app/\(shell\)/memories/page.tsx
import { redirect } from "next/navigation"

export default function MemoriesPage() {
  redirect("/chat")
}
