import { clerkMiddleware } from "@clerk/nextjs/server"

// clerkMiddleware() must run so auth() works in Server Components/layouts —
// route protection itself happens at the page level.
export default clerkMiddleware()

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|woff|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
}
