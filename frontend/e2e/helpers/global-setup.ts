import fs from "fs"
import path from "path"
import { clerkSetup } from "@clerk/testing/playwright"

export default async function globalSetup() {
  // Load .env.local into process.env so CLERK_SECRET_KEY and
  // NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY are available to auth helpers.
  // (Next.js loads these automatically for the app; the test runner does not.)
  for (const envFile of [".env.local", ".env.test"]) {
    const fullPath = path.resolve(process.cwd(), envFile)
    if (!fs.existsSync(fullPath)) continue
    const lines = fs.readFileSync(fullPath, "utf-8").split("\n")
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith("#")) continue
      const eq = trimmed.indexOf("=")
      if (eq === -1) continue
      const key = trimmed.slice(0, eq).trim()
      const val = trimmed.slice(eq + 1).trim()
      if (key && !process.env[key]) process.env[key] = val
    }
  }

  // Initialise Clerk testing mode so setupClerkTestingToken works per-test.
  await clerkSetup()
}
