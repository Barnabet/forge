import type { StreamItem } from '../state/reducer'

export type ToolItem = Extract<StreamItem, { kind: 'tool' }>

// Family key per engine tool; unknown tools get their own family so they
// only ever group with themselves.
const FAMILY: Record<string, string> = {
  read_file: 'read',
  write_file: 'edit',
  edit_file: 'edit',
  bash: 'run',
  grep: 'search',
  glob: 'search',
  list_dir: 'search',
  load_skill: 'skill',
}

export function familyOf(tool: string): string {
  return FAMILY[tool] ?? `other:${tool}`
}

// [present, past] per tool, for single lines.
const VERBS: Record<string, [string, string]> = {
  read_file: ['Reading', 'Read'],
  write_file: ['Writing', 'Wrote'],
  edit_file: ['Editing', 'Edited'],
  bash: ['Running', 'Ran'],
  grep: ['Searching', 'Searched'],
  glob: ['Searching', 'Searched'],
  list_dir: ['Listing', 'Listed'],
  load_skill: ['Loading', 'Loaded'],
}

export function toolVerb(item: ToolItem): string {
  const [present, past] = VERBS[item.tool] ?? ['Running', 'Ran']
  return item.status === 'running' ? present : past
}

// Group label: verb conjugates on whether any member is still running;
// read/edit count unique files (the same file read twice is still 1 file).
const GROUP: Record<string, { verbs: [string, string]; noun: [string, string] }> = {
  read: { verbs: ['Reading', 'Read'], noun: ['file', 'files'] },
  edit: { verbs: ['Editing', 'Edited'], noun: ['file', 'files'] },
  run: { verbs: ['Running', 'Ran'], noun: ['command', 'commands'] },
  search: { verbs: ['Running', 'Ran'], noun: ['search', 'searches'] },
  skill: { verbs: ['Loading', 'Loaded'], noun: ['skill', 'skills'] },
}

export function groupLabel(items: ToolItem[]): string {
  const fam = familyOf(items[0].tool)
  const running = items.some(i => i.status === 'running')
  const def = GROUP[fam]
  if (!def) {
    return `${running ? 'Running' : 'Ran'} ${items[0].tool} × ${items.length}`
  }
  const n = fam === 'read' || fam === 'edit'
    ? new Set(items.map(i => i.display)).size
    : items.length
  return `${def.verbs[running ? 0 : 1]} ${n} ${def.noun[n === 1 ? 0 : 1]}`
}

// Stable keys: the reducer splices items out of the middle (allowed gates,
// empty streaming prose), so index keys would let React reuse a removed item's
// instance — and its internal state — for the next same-kind item. Tools and
// gates carry callId; other kinds use seq. Streaming prose has seq 0 and falls
// back to index: it is only ever the single item at the tail of the list.
const itemKey = (item: StreamItem, i: number): string =>
  item.kind === 'gate' ? `gate:${item.callId}`
  : item.seq > 0 ? `s:${item.seq}`
  : `i:${i}`

export type RenderEntry =
  | { kind: 'tools'; key: string; groups: ToolItem[][] }
  | { kind: 'item'; key: string; item: StreamItem }

// Partition the stream for rendering: consecutive tool items form one
// activity block, grouped by family in first-seen order (a run of tools
// comes from one assistant turn, so ordering within it carries no meaning).
// Edits group per file — repeated edits to one file roll up; edits to
// different files stay separate lines. Any non-tool item — prose, user,
// gate, error — breaks the run.
export function segmentItems(items: StreamItem[]): RenderEntry[] {
  const out: RenderEntry[] = []
  let buffer: ToolItem[] = []

  const flush = () => {
    if (!buffer.length) return
    const byFamily = new Map<string, ToolItem[]>()
    for (const it of buffer) {
      const f = familyOf(it.tool)
      const fam = f === 'edit' ? `edit:${it.display}` : f
      const g = byFamily.get(fam)
      if (g) g.push(it)
      else byFamily.set(fam, [it])
    }
    out.push({ kind: 'tools', key: `t:${buffer[0].callId}`, groups: [...byFamily.values()] })
    buffer = []
  }

  items.forEach((item, i) => {
    if (item.kind === 'tool') {
      buffer.push(item)
    } else {
      flush()
      out.push({ kind: 'item', key: itemKey(item, i), item })
    }
  })
  flush()
  return out
}
