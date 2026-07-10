import type { Metadata } from "next";
import localFont from "next/font/local";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

const DESCRIPTION =
  "AI-powered financial research: filings, market data, and company analysis in one conversation.";

export const metadata: Metadata = {
  // Set NEXT_PUBLIC_APP_URL to the deployed origin so OG/Twitter image URLs
  // resolve absolutely in link previews.
  metadataBase: new URL(process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000"),
  title: {
    default: "FinCopilot — AI financial research",
    template: "%s · FinCopilot",
  },
  description: DESCRIPTION,
  applicationName: "FinCopilot",
  openGraph: {
    title: "FinCopilot — AI financial research",
    description: DESCRIPTION,
    siteName: "FinCopilot",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "FinCopilot — AI financial research",
    description: DESCRIPTION,
  },
};

// Single source of truth for Clerk theming (auth pages + UserButton popover).
// Values mirror the .dark tokens in globals.css; Clerk renders in its own
// scope so the tokens are referenced via var() where possible.
const clerkAppearance = {
  variables: {
    colorPrimary: "hsl(172, 66%, 50%)",
    colorTextOnPrimaryBackground: "hsl(180, 60%, 6%)",
    colorBackground: "hsl(220, 14%, 11%)",
    colorText: "hsl(220, 14%, 93%)",
    colorTextSecondary: "hsl(220, 9%, 62%)",
    colorNeutral: "hsl(220, 14%, 93%)",
    colorInputBackground: "hsl(220, 12%, 15%)",
    colorInputText: "hsl(220, 14%, 93%)",
    colorDanger: "hsl(0, 70%, 52%)",
    borderRadius: "0.5rem",
  },
  elements: {
    card: "border border-border shadow-2xl",
    userButtonPopoverCard: "bg-card border border-border shadow-lg",
    userButtonPopoverActionButton: "text-foreground hover:bg-muted",
    userButtonPopoverActionButtonText: "text-foreground",
    userButtonPopoverActionButtonIcon: "text-muted-foreground",
    userButtonPopoverFooter: "hidden",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased`}>
        <ClerkProvider appearance={clerkAppearance}>
          {children}
        </ClerkProvider>
      </body>
    </html>
  );
}
