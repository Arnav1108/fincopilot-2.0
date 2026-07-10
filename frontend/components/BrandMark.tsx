// FinCopilot brand mark: teal rounded square with an upward spark line.
// Pure SVG so it inherits crisp rendering at any size (sidebar, auth, OG).
export default function BrandMark({ size = 24 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="32" height="32" rx="8" fill="url(#fincopilot-mark-gradient)" />
      <path
        d="M7.5 21.5 L13 15.5 L17 18.5 L24.5 9.5"
        stroke="hsl(180, 60%, 8%)"
        strokeWidth="2.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24.5" cy="9.5" r="2" fill="hsl(180, 60%, 8%)" />
      <defs>
        <linearGradient
          id="fincopilot-mark-gradient"
          x1="0"
          y1="0"
          x2="32"
          y2="32"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="hsl(172, 70%, 56%)" />
          <stop offset="1" stopColor="hsl(186, 70%, 40%)" />
        </linearGradient>
      </defs>
    </svg>
  )
}
