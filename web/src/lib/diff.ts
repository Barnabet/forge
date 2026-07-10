export interface DiffLine {
  kind: 'add' | 'del' | 'ctx'
  oldNo: number | null
  newNo: number | null
  text: string
}

export interface Hunk {
  header: string
  lines: DiffLine[]
}

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

export function parseUnifiedDiff(diff: string): Hunk[] {
  const hunks: Hunk[] = []
  let current: Hunk | null = null
  let oldNo = 0
  let newNo = 0

  for (const line of diff.split('\n')) {
    const m = HUNK_RE.exec(line)
    if (m) {
      current = { header: line, lines: [] }
      hunks.push(current)
      oldNo = parseInt(m[1], 10)
      newNo = parseInt(m[2], 10)
      continue
    }
    if (!current || line.startsWith('---') || line.startsWith('+++')) continue
    if (line.startsWith('+')) {
      current.lines.push({ kind: 'add', oldNo: null, newNo: newNo++, text: line.slice(1) })
    } else if (line.startsWith('-')) {
      current.lines.push({ kind: 'del', oldNo: oldNo++, newNo: null, text: line.slice(1) })
    } else if (line.startsWith(' ') || line === '') {
      if (line === '' && current.lines.length === 0) continue
      current.lines.push({ kind: 'ctx', oldNo: oldNo++, newNo: newNo++, text: line.slice(1) })
    }
  }
  // difflib ends with a trailing newline → one spurious empty ctx line; drop it
  for (const h of hunks) {
    const last = h.lines[h.lines.length - 1]
    if (last?.kind === 'ctx' && last.text === '') h.lines.pop()
  }
  return hunks
}
