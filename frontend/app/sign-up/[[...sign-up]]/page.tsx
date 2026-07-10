import { SignUp } from "@clerk/nextjs";
import BrandMark from "@/components/BrandMark";

export default function SignUpPage() {
  return (
    <main className="relative flex min-h-screen flex-col items-center justify-center gap-8 bg-background px-4 py-12">
      {/* Soft brand glow behind the card */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_60%_40%_at_50%_0%,hsl(172_66%_50%/0.10),transparent)]"
      />
      <div className="relative flex flex-col items-center gap-3 text-center">
        <BrandMark size={44} />
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          FinCopilot
        </h1>
        <p className="max-w-xs text-sm text-muted-foreground">
          AI-powered research over filings, market data, and company
          financials.
        </p>
      </div>
      <div className="relative">
        <SignUp />
      </div>
    </main>
  );
}
