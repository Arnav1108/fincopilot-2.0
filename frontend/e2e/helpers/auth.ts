import { clerk } from "@clerk/testing/playwright"
import type { Page } from "@playwright/test"

/**
 * Authenticates the browser page using Clerk's admin sign-in token flow.
 *
 * How it works:
 *   clerk.signIn internally:
 *     1. Registers a Playwright route handler on Clerk's FAPI so the testing
 *        token is appended to all FAPI requests (bypasses bot/CAPTCHA checks).
 *     2. Waits for window.Clerk to load (ClerkProvider must already be on the page).
 *     3. Calls Clerk Backend API to create a short-lived sign-in ticket for the
 *        user, then signs in via the ticket strategy — no password required.
 *     4. Waits for window.Clerk.user to be non-null.
 *
 *   Why we navigate to /sign-in first:
 *     clerk.signIn needs window.Clerk to exist. Navigating directly to /chat
 *     triggers server-side middleware (auth.protect()) which redirects to
 *     Clerk's hosted sign-in before ClerkProvider ever mounts — so
 *     window.Clerk is never available. /sign-in is a public route; ClerkProvider
 *     mounts there and Clerk JS loads.
 *
 *   Why we wait for window.Clerk.session after navigating to /chat:
 *     Each page.goto() triggers a full page reload. After reload, Clerk JS
 *     re-hydrates the session asynchronously. If a test clicks "New Chat"
 *     before hydration completes, useAuth().getToken() returns null and the
 *     conversation creation API call is silently skipped (no navigation).
 *     Waiting for window.Clerk.session guarantees the token is ready
 *     immediately when setupClerkAuth returns.
 *
 * Test user: reads TEST_USER_EMAIL from env, or falls back to the first user
 * in the Clerk instance (via Backend API). Requires CLERK_SECRET_KEY.
 */
async function resolveTestEmail(): Promise<string> {
  if (process.env.TEST_USER_EMAIL) return process.env.TEST_USER_EMAIL

  const { createClerkClient } = await import("@clerk/backend")
  const clerkClient = createClerkClient({ secretKey: process.env.CLERK_SECRET_KEY! })
  const { data: users } = await clerkClient.users.getUserList({ limit: 1 })
  if (!users.length) {
    throw new Error(
      "No users found in the Clerk instance.\n" +
        "  Set TEST_USER_EMAIL in frontend/.env.local, or create a user in the Clerk dashboard.",
    )
  }
  const email = users[0].emailAddresses[0]?.emailAddress
  if (!email) {
    throw new Error(
      "First Clerk user has no email address.\n" +
        "  Set TEST_USER_EMAIL in frontend/.env.local.",
    )
  }
  return email
}

export async function setupClerkAuth(page: Page): Promise<void> {
  const email = await resolveTestEmail()

  // Navigate to sign-in (public route) so ClerkProvider mounts and
  // window.Clerk becomes available before clerk.signIn waits for it.
  await page.goto("/sign-in")

  // Signs in via Backend API ticket — no password, no CAPTCHA.
  await clerk.signIn({ page, emailAddress: email })

  // Navigate to /chat and wait for Clerk to fully re-hydrate the session.
  // Without this wait, window.Clerk.session is briefly null after the page
  // reload, causing getToken() to return null and silently aborting API calls.
  await page.goto("/chat")
  await page.waitForFunction(
    () => !!(window as any).Clerk?.session,
    { timeout: 15_000 },
  )
}
