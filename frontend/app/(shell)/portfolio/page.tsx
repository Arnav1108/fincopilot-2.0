// Portfolio page disabled. The real UI lives one commit before the stub was
// introduced: git checkout bd6ffda^ -- app/\(shell\)/portfolio/page.tsx
import { redirect } from "next/navigation"

export default function PortfolioPage() {
  redirect("/chat")
}
