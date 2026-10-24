// Unified-diff parsing for rendering findings against their real diff lines.
// Mirrors app/utils/diff_validator.py so the UI and GitHub agree on what is
// anchorable.

export type DiffLineKind = "add" | "del" | "context"

export interface DiffLine {
  kind: DiffLineKind
  newNumber: number | null
  text: string
}

export interface Hunk {
  header: string
  lines: DiffLine[]
}

const HUNK_HEADER_RE = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/

export function parsePatch(patch: string): Hunk[] {
  const hunks: Hunk[] = []
  let next = 0
  for (const raw of patch.split("\n")) {
    const header = HUNK_HEADER_RE.exec(raw)
    if (header) {
      next = Number(header[1])
      hunks.push({ header: raw, lines: [] })
      continue
    }
    const hunk = hunks.at(-1)
    if (!hunk) continue
    if (raw.startsWith("+")) hunk.lines.push({ kind: "add", newNumber: next++, text: raw.slice(1) })
    else if (raw.startsWith(" ")) hunk.lines.push({ kind: "context", newNumber: next++, text: raw.slice(1) })
    else if (raw.startsWith("-")) hunk.lines.push({ kind: "del", newNumber: null, text: raw.slice(1) })
  }
  return hunks
}

/** New-side line numbers a GitHub review comment can attach to. */
export function anchorableLines(patch: string): Set<number> {
  const lines = new Set<number>()
  for (const hunk of parsePatch(patch))
    for (const l of hunk.lines) if (l.newNumber !== null) lines.add(l.newNumber)
  return lines
}

/** The hunk lines around [start, end], with `pad` lines of context either side. */
export function contextWindow(
  hunks: Hunk[],
  start: number,
  end: number,
  pad = 2,
): DiffLine[] | null {
  for (const hunk of hunks) {
    const first = hunk.lines.findIndex((l) => l.newNumber === start)
    const last = hunk.lines.findIndex((l) => l.newNumber === end)
    if (first === -1 || last === -1) continue
    return hunk.lines.slice(Math.max(0, first - pad), last + pad + 1)
  }
  return null
}

/** Current text of new-file lines [start, end], used as the "before" of a suggestion. */
export function newSideText(hunks: Hunk[], start: number, end: number): string[] {
  const out: string[] = []
  for (const hunk of hunks)
    for (const l of hunk.lines)
      if (l.newNumber !== null && l.newNumber >= start && l.newNumber <= end) out.push(l.text)
  return out
}
