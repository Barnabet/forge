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
  web_search: 'web',
  fetch_page: 'web',
  read_pdf: 'pdf',
  view: 'view',
  create_image: 'image',
  update_todos: 'todos',
  spawn_agents: 'delegate',
  remember: 'memory',
  read_memory: 'memory',
}

export function familyOf(tool: string): string {
  return FAMILY[tool] ?? `other:${tool}`
}

// Every tool call moves through three states, and each names the same action
// in a different tense:
//   about   — argument stream announced the call but the body hasn't landed
//             ("About to read")
//   gerund  — the call is executing ("Reading")
//   past    — the call returned ("Read")
export type ToolState = 'about' | 'gerund' | 'past'

export type VerbForms = { about: string; gerund: string; past: string }

const DEFAULT_VERB: VerbForms = { about: 'About to run', gerund: 'Running', past: 'Ran' }

// Per-tool phrasing for single lines. The `about` phrase is a full "About to …"
// clause so irregular verbs read naturally.
const VERBS: Record<string, VerbForms> = {
  read_file: { about: 'About to read', gerund: 'Reading', past: 'Read' },
  write_file: { about: 'About to write', gerund: 'Writing', past: 'Wrote' },
  edit_file: { about: 'About to edit', gerund: 'Editing', past: 'Edited' },
  bash: { about: 'About to run', gerund: 'Running', past: 'Ran' },
  grep: { about: 'About to search', gerund: 'Searching', past: 'Searched' },
  glob: { about: 'About to search', gerund: 'Searching', past: 'Searched' },
  list_dir: { about: 'About to list', gerund: 'Listing', past: 'Listed' },
  load_skill: { about: 'About to load', gerund: 'Loading', past: 'Loaded' },
  web_search: { about: 'About to search web for', gerund: 'Searching web for', past: 'Searched web for' },
  fetch_page: { about: 'About to fetch', gerund: 'Fetching', past: 'Fetched' },
  read_pdf: { about: 'About to read', gerund: 'Reading PDF', past: 'Read PDF' },
  view: { about: 'About to view', gerund: 'Viewing', past: 'Viewed' },
  create_image: { about: 'About to generate image', gerund: 'Generating image', past: 'Generated image' },
  update_todos: { about: 'About to update todos', gerund: 'Updating todos', past: 'Updated todos' },
  spawn_agents: { about: 'About to delegate', gerund: 'Delegating', past: 'Delegated' },
  remember: { about: 'About to recall', gerund: 'Recalling', past: 'Recalled' },
  read_memory: { about: 'About to read memory', gerund: 'Reading memory', past: 'Read memory' },
}

// A tool line's state: pending calls are still streaming their arguments,
// running calls are executing, everything else has returned.
export function stateOf(item: ToolItem): ToolState {
  if (item.pending) return 'about'
  return item.status === 'running' ? 'gerund' : 'past'
}

export function conjugate(tool: string, state: ToolState): string {
  return (VERBS[tool] ?? DEFAULT_VERB)[state]
}

// Presentation only: show paths relative to the session cwd. Applies to
// every occurrence (bash commands embed paths too); paths outside the
// workspace keep their absolute form.
export function relDisplay(display: string, cwd: string): string {
  if (!cwd) return display
  const prefix = cwd.endsWith('/') ? cwd : `${cwd}/`
  const out = display.split(prefix).join('')
  return out || '.'
}

export function toolVerb(item: ToolItem): string {
  return conjugate(item.tool, stateOf(item))
}

// A group's state is the earliest state any member still occupies: all
// pending → about, any still executing → gerund, otherwise past.
export function groupState(items: ToolItem[]): ToolState {
  if (items.every(i => i.pending)) return 'about'
  if (items.some(i => i.status === 'running')) return 'gerund'
  return 'past'
}

// Group label: verb conjugates on the group state; read/edit count unique
// files (the same file read twice is still 1 file).
const GROUP: Record<string, { verb: VerbForms; noun: [string, string] }> = {
  read: { verb: { about: 'About to read', gerund: 'Reading', past: 'Read' }, noun: ['file', 'files'] },
  edit: { verb: { about: 'About to edit', gerund: 'Editing', past: 'Edited' }, noun: ['file', 'files'] },
  run: { verb: { about: 'About to run', gerund: 'Running', past: 'Ran' }, noun: ['command', 'commands'] },
  search: { verb: { about: 'About to run', gerund: 'Running', past: 'Ran' }, noun: ['search', 'searches'] },
  skill: { verb: { about: 'About to load', gerund: 'Loading', past: 'Loaded' }, noun: ['skill', 'skills'] },
  web: { verb: { about: 'About to browse', gerund: 'Browsing', past: 'Browsed' }, noun: ['page', 'pages'] },
  pdf: { verb: { about: 'About to open', gerund: 'Opening', past: 'Opened' }, noun: ['PDF', 'PDFs'] },
  view: { verb: { about: 'About to view', gerund: 'Viewing', past: 'Viewed' }, noun: ['item', 'items'] },
  image: { verb: { about: 'About to generate', gerund: 'Generating', past: 'Generated' }, noun: ['image', 'images'] },
  todos: { verb: { about: 'About to update', gerund: 'Updating', past: 'Updated' }, noun: ['todo list', 'todo lists'] },
  delegate: { verb: { about: 'About to delegate', gerund: 'Delegating', past: 'Delegated' }, noun: ['spawn', 'spawns'] },
  memory: { verb: { about: 'About to recall', gerund: 'Recalling', past: 'Recalled' }, noun: ['memory', 'memories'] },
}

export function groupLabel(items: ToolItem[]): string {
  const fam = familyOf(items[0].tool)
  const state = groupState(items)
  const def = GROUP[fam]
  if (!def) {
    return `${conjugate(items[0].tool, state)} ${items[0].tool} × ${items.length}`
  }
  const n = fam === 'read' || fam === 'edit'
    ? new Set(items.map(i => i.display)).size
    : items.length
  return `${def.verb[state]} ${n} ${def.noun[n === 1 ? 0 : 1]}`
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
// activity block, and ADJACENT same-family calls within it roll up into one
// group. Grouping is strictly consecutive — never hoisted into an earlier
// group of the same family — so a call arriving live always appends at the
// tail instead of rewriting lines above it (which flashed and shifted text).
// Edits group per file — repeated edits to one file roll up; edits to
// different files stay separate lines. Any non-tool item — prose, user,
// gate, error — breaks the run.
export function segmentItems(items: StreamItem[]): RenderEntry[] {
  const out: RenderEntry[] = []
  let buffer: ToolItem[] = []

  const flush = () => {
    if (!buffer.length) return
    const groups: ToolItem[][] = []
    let lastFam: string | null = null
    for (const it of buffer) {
      const f = familyOf(it.tool)
      const fam = f === 'edit' ? `edit:${it.display}` : f
      if (fam === lastFam) groups[groups.length - 1].push(it)
      else groups.push([it])
      lastFam = fam
    }
    out.push({ kind: 'tools', key: `t:${buffer[0].callId}`, groups })
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
