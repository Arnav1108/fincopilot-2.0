import { auth } from "@clerk/nextjs/server"
import { redirect } from "next/navigation"
import { ConversationsProvider } from "@/hooks/useConversations"
import Sidebar from "@/components/sidebar/Sidebar"

export default async function ShellLayout({ children }: { children: React.ReactNode }) {
  const { userId } = await auth()
  if (!userId) redirect("/sign-in")

  return (
    <ConversationsProvider>
      <div className="flex h-screen bg-background text-foreground overflow-hidden">
        <Sidebar />
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {children}
        </main>
      </div>
    </ConversationsProvider>
  )
}
