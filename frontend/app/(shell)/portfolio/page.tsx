// Portfolio page disabled. To restore: git checkout HEAD -- app/\(shell\)/portfolio/page.tsx
import { redirect } from "next/navigation"

export default function PortfolioPage() {
  redirect("/chat")
}
