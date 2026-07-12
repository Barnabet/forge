import type { TerminalOutput } from '../protocol'

export type TerminalLifecycle = 'starting' | 'running' | 'exited' | 'closed' | 'orphaned'

// Structural shape shared by the durable TerminalState event and the REST
// snapshot returned by GET /terminals — both upsert a record's metadata.
export interface TerminalMeta {
  terminal_id: string
  command: string[]
  cwd: string
  cols: number
  rows: number
  state: TerminalLifecycle
  // Pydantic-default fields on the wire; consumed with ?? at the call site.
  output_offset?: number
  exit_code?: number | null
  exit_reason?: string | null
}

export interface TerminalRecord {
  id: string
  command: string[]
  cwd: string
  cols: number
  rows: number
  state: TerminalLifecycle
  exitCode: number | null
  exitReason: string | null
  // Rendered/retained output. Its UTF-8 byte length always equals
  // endOffset - startOffset, so byte cursors stay coherent with the string.
  output: string
  startOffset: number
  endOffset: number
  // Set when a gap is detected (a chunk starting past endOffset) or when a
  // terminal is first learned about with pre-existing output: a REST buffer
  // fetch is required to fill in before appending live output is safe.
  needsHydration: boolean
  loading: boolean
  error: string | null
  // Activity marker for a later dock: output arrived that the user hasn't
  // looked at. Cleared on select / clear.
  unread: boolean
}

export interface SessionTerminals {
  records: Record<string, TerminalRecord>
  order: string[]
}

export function emptyTerminals(): SessionTerminals {
  return { records: {}, order: [] }
}

const _enc = new TextEncoder()
const _dec = new TextDecoder()

// JS strings index by UTF-16 code units, but terminal offsets are UTF-8 byte
// cursors: derive overlap by encoded byte length, never by char count.
export function utf8Len(s: string): number {
  return _enc.encode(s).length
}

// Return the substring beginning `byteStart` UTF-8 bytes into `s`. Boundaries
// always fall on whole characters here because the server only emits offsets at
// decoded-output boundaries, so decoding the byte tail never splits a rune.
function sliceFromByte(s: string, byteStart: number): string {
  if (byteStart <= 0) return s
  const bytes = _enc.encode(s)
  if (byteStart >= bytes.length) return ''
  return _dec.decode(bytes.subarray(byteStart))
}

function baseRecord(id: string): TerminalRecord {
  return {
    id, command: [], cwd: '', cols: 80, rows: 24, state: 'starting',
    exitCode: null, exitReason: null, output: '', startOffset: 0, endOffset: 0,
    needsHydration: false, loading: false, error: null, unread: false,
  }
}

// Offset-aware merge of one output chunk. Idempotent: replayed/overlapping
// chunks append only their unseen suffix, fully-seen chunks are dropped, and a
// chunk beyond the current end flags the record for hydration instead of
// concatenating corrupt output across the gap.
export function mergeOutput(
  rec: TerminalRecord, start: number, end: number, text: string,
): TerminalRecord {
  if (end <= rec.endOffset) return rec // fully seen already
  if (start > rec.endOffset) {
    // Gap: our data ends before this chunk begins. Do not append; resync.
    return rec.needsHydration ? rec : { ...rec, needsHydration: true }
  }
  const seen = rec.endOffset - start // bytes of this chunk already appended
  const suffix = seen <= 0 ? text : sliceFromByte(text, seen)
  return { ...rec, output: rec.output + suffix, endOffset: end, unread: true }
}

// Upsert metadata/lifecycle from a durable TerminalState (or REST snapshot).
// Never touches output/offsets on an existing record, so a replayed older
// snapshot can never regress output the ephemeral stream has since advanced.
export function upsertTerminalState(
  col: SessionTerminals, e: TerminalMeta,
): SessionTerminals {
  const prev = col.records[e.terminal_id]
  const rec: TerminalRecord = {
    ...(prev ?? baseRecord(e.terminal_id)),
    command: e.command, cwd: e.cwd, cols: e.cols, rows: e.rows, state: e.state,
    exitCode: e.exit_code ?? null, exitReason: e.exit_reason ?? null,
  }
  // A newly-discovered terminal that already produced output must be hydrated
  // from REST; never fabricate offsets from output_offset (that would desync
  // the byte-merge and duplicate/lose data).
  if (!prev && (e.output_offset ?? 0) > 0) rec.needsHydration = true
  return {
    records: { ...col.records, [e.terminal_id]: rec },
    order: prev ? col.order : [...col.order, e.terminal_id],
  }
}

// Apply one ephemeral TerminalOutput event. Creates a placeholder (flagged for
// hydration if it joined mid-stream) for output about an unknown terminal.
export function applyTerminalOutput(
  col: SessionTerminals, e: TerminalOutput,
): SessionTerminals {
  const prev = col.records[e.terminal_id]
  const rec = prev ?? baseRecord(e.terminal_id)
  const merged = mergeOutput(rec, e.start_offset, e.end_offset, e.text)
  if (prev && merged === rec) return col
  return {
    records: { ...col.records, [e.terminal_id]: merged },
    order: prev ? col.order : [...col.order, e.terminal_id],
  }
}

export interface TerminalBuffer {
  text: string
  start_offset: number
  end_offset: number
  dropped: boolean
}

// Fold a REST buffer read (GET /terminals/{tid}?after=N) into a record. Races
// with the WS are safe: a snapshot the stream already surpassed only clears the
// hydration flags, an overlapping snapshot merges its suffix, and a dropped /
// gapped snapshot replaces the retained view with the authoritative buffer.
export function applyTerminalBuffer(
  col: SessionTerminals, tid: string, buf: TerminalBuffer,
): SessionTerminals {
  const prev = col.records[tid]
  const rec = prev ?? baseRecord(tid)
  let next: TerminalRecord
  if (buf.end_offset <= rec.endOffset) {
    next = { ...rec, needsHydration: false, loading: false, error: null }
  } else if (buf.dropped || buf.start_offset > rec.endOffset) {
    next = {
      ...rec, output: buf.text, startOffset: buf.start_offset,
      endOffset: buf.end_offset, needsHydration: false, loading: false, error: null,
    }
  } else {
    next = {
      ...mergeOutput(rec, buf.start_offset, buf.end_offset, buf.text),
      needsHydration: false, loading: false, error: null,
    }
  }
  return {
    records: { ...col.records, [tid]: next },
    order: prev ? col.order : [...col.order, tid],
  }
}

export function patchTerminal(
  col: SessionTerminals, tid: string, patch: Partial<TerminalRecord>,
): SessionTerminals {
  const prev = col.records[tid]
  if (!prev) return col
  return { ...col, records: { ...col.records, [tid]: { ...prev, ...patch } } }
}
