"use client"

// Root fallback: replaces the entire document when the root layout itself
// throws, so it must render its own <html>/<body> and use no app CSS.
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <html lang="en" className="dark">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "12px",
          backgroundColor: "#0f172a",
          color: "#e2e8f0",
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
          fontSize: "14px",
        }}
      >
        <p>Something went wrong.</p>
        <button
          type="button"
          onClick={reset}
          style={{
            padding: "6px 12px",
            borderRadius: "6px",
            border: "1px solid #334155",
            backgroundColor: "transparent",
            color: "#94a3b8",
            cursor: "pointer",
            fontSize: "14px",
          }}
        >
          Try again
        </button>
      </body>
    </html>
  )
}
