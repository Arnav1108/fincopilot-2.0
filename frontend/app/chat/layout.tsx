import { UserButton } from "@clerk/nextjs"
import { ConversationsProvider } from "@/hooks/useConversations"
import Sidebar from "@/components/sidebar/Sidebar"

export default function ChatLayout({ children }: { children: React.ReactNode }) {
  return (
    <ConversationsProvider>
      <div className="flex h-screen bg-background text-foreground overflow-hidden">
        <Sidebar />
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden relative">
          <div className="absolute top-3 right-3 z-10">
            <UserButton />
          </div>
          {children}
        </main>
      </div>
    </ConversationsProvider>
  )
}
