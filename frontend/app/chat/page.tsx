import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

export default async function ChatPage() {
  const { userId } = await auth();

  if (!userId) {
    redirect("/sign-in");
  }

  return (
    <main className="flex min-h-screen items-center justify-center">
      <div className="text-xl font-medium text-slate-700">Chat coming soon</div>
    </main>
  );
}
