import { auth } from "@clerk/nextjs/server"
import { redirect } from "next/navigation"
import { listConversations } from "@/lib/api"
import ChatEmptyState from "@/components/chat/ChatEmptyState"

export default async function ChatPage() {
  const { userId, getToken } = await auth()
  if (!userId) redirect("/sign-in")

  const token = await getToken()
  if (!token) redirect("/sign-in")

  // If listing fails, fall through to the empty state rather than crashing
  // the page — the user can still start a new conversation.
  const conversations = await listConversations(token).catch(() => [])

  if (conversations.length > 0) {
    redirect(`/chat/${conversations[0].id}`)
  }

  return <ChatEmptyState />
}
