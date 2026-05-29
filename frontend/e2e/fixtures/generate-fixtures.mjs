/**
 * Generates e2e test fixtures.  Run once before the test suite:
 *   node e2e/fixtures/generate-fixtures.mjs
 *
 * Produces: e2e/fixtures/sample.pdf
 *
 * The PDF is built byte-by-byte so xref offsets are always exact,
 * regardless of platform line endings.
 */

import { writeFileSync } from "fs"
import { fileURLToPath } from "url"
import { dirname, join } from "path"

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

// ── PDF builder ────────────────────────────────────────────────────────────────

function buildPdf(bodyText) {
  /** @type {Buffer[]} */
  const chunks = []
  /** @type {Record<number,number>} object-id → byte offset */
  const offsets = {}
  let pos = 0

  /** Append an ASCII string, tracking the current byte position. */
  const w = (s) => {
    const b = Buffer.from(s, "ascii")
    chunks.push(b)
    pos += b.length
  }

  // Escape special PDF string characters
  const esc = (t) => t.replace(/[\\()]/g, (c) => "\\" + c)

  // ── Header ──────────────────────────────────────────────────────────────────
  w("%PDF-1.4\n")

  // ── Object 1: Document Catalog ───────────────────────────────────────────────
  offsets[1] = pos
  w("1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")

  // ── Object 2: Pages dictionary ───────────────────────────────────────────────
  offsets[2] = pos
  w("2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n")

  // ── Object 3: Page (references content stream + embedded Helvetica font) ─────
  offsets[3] = pos
  w(
    "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]" +
      "/Contents 4 0 R" +
      "/Resources<</Font<</F1<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>>>>>" +
      ">>endobj\n",
  )

  // ── Object 4: Content stream ──────────────────────────────────────────────────
  const stream = `BT /F1 12 Tf 72 720 Td (${esc(bodyText)}) Tj ET`
  offsets[4] = pos
  w(`4 0 obj<</Length ${stream.length}>>stream\n`)
  w(stream)
  w("\nendstream\nendobj\n")

  // ── Cross-reference table ─────────────────────────────────────────────────────
  const xrefPos = pos
  const numObjs = 5 // objects 0-4

  w(`xref\n0 ${numObjs}\n`)
  // Free object (always offset 0, gen 65535)
  w("0000000000 65535 f \n")
  // Objects 1-4: each xref entry must be exactly 20 bytes
  // Format: nnnnnnnnnn ggggg n \n  (10 + 1 + 5 + 1 + 1 + 1 + \n = 20)
  for (let i = 1; i < numObjs; i++) {
    w(offsets[i].toString().padStart(10, "0") + " 00000 n \n")
  }

  w(`trailer<</Size ${numObjs}/Root 1 0 R>>\n`)
  w(`startxref\n${xrefPos}\n%%EOF`)

  return Buffer.concat(chunks)
}

// ── Generate ──────────────────────────────────────────────────────────────────

const pdf = buildPdf(
  "FinCopilot E2E test document. " +
    "This document discusses the Price to Earnings (P/E) ratio. " +
    "The P/E ratio measures a company's share price relative to its earnings per share. " +
    "A high P/E may indicate growth expectations; a low P/E may indicate undervaluation.",
)

const outPath = join(__dirname, "sample.pdf")
writeFileSync(outPath, pdf)
console.log(`Generated ${outPath} — ${pdf.length} bytes`)
