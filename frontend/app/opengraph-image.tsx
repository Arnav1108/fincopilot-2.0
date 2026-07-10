import { ImageResponse } from "next/og"

export const runtime = "edge"
export const alt = "FinCopilot — AI financial research"
export const size = { width: 1200, height: 630 }
export const contentType = "image/png"

// Link-preview card for Slack/LinkedIn/Twitter — mirrors the in-app dark
// theme and brand mark so the share surface matches the product.
// Colors are hex, not hsl(): satori's gradient parser rejects hsl() stops.
export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 28,
          backgroundColor: "#0f1115",
          backgroundImage:
            "radial-gradient(ellipse 80% 60% at 50% 0%, rgba(45, 212, 191, 0.14), transparent)",
        }}
      >
        <div
          style={{
            display: "flex",
            width: 112,
            height: 112,
            borderRadius: 28,
            alignItems: "center",
            justifyContent: "center",
            background: "linear-gradient(135deg, #40ddc8, #1f9fad)",
          }}
        >
          <svg width="72" height="72" viewBox="0 0 32 32" fill="none">
            <path
              d="M7.5 21.5 L13 15.5 L17 18.5 L24.5 9.5"
              stroke="#06201f"
              strokeWidth="2.75"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle cx="24.5" cy="9.5" r="2" fill="#06201f" />
          </svg>
        </div>
        <div
          style={{
            fontSize: 72,
            fontWeight: 700,
            color: "#f1f3f5",
            letterSpacing: "-0.03em",
          }}
        >
          FinCopilot
        </div>
        <div
          style={{
            fontSize: 30,
            color: "#9aa1ac",
            maxWidth: 760,
            textAlign: "center",
          }}
        >
          AI-powered research over filings, market data, and company financials
        </div>
      </div>
    ),
    { ...size },
  )
}
