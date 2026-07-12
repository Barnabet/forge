import { describe, expect, it } from 'vitest'
import {
  applyTerminalBuffer, applyTerminalOutput, emptyTerminals, mergeOutput,
  upsertTerminalState, utf8Len, type TerminalRecord,
} from './terminals'
import type { TerminalMeta } from './terminals'
import type { TerminalOutput } from '../protocol'

const meta = (id: string, over: Partial<TerminalMeta> = {}): TerminalMeta => ({
  terminal_id: id, command: ['sh'], cwd: '/w', cols: 80, rows: 24,
  state: 'running', ...over,
})
const out = (id: string, start: number, end: number, text: string): TerminalOutput =>
  ({ type: 'terminal_output', session_id: 's1', ts: 0, seq: 0,
     terminal_id: id, start_offset: start, end_offset: end, text })
const rec = (over: Partial<TerminalRecord> = {}): TerminalRecord => ({
  id: 't', command: [], cwd: '', cols: 80, rows: 24, state: 'running',
  exitCode: null, exitReason: null, output: '', startOffset: 0, endOffset: 0,
  needsHydration: false, loading: false, error: null, unread: false, ...over,
})

describe('mergeOutput', () => {
  it('appends contiguous chunks', () => {
    const r = mergeOutput(rec(), 0, 3, 'abc')
    expect(r.output).toBe('abc')
    expect(r.endOffset).toBe(3)
    expect(r.unread).toBe(true)
  })
  it('drops a fully-seen duplicate chunk', () => {
    const base = rec({ output: 'abc', endOffset: 3 })
    expect(mergeOutput(base, 0, 3, 'abc')).toBe(base)
  })
  it('appends only the unseen suffix of an overlapping chunk', () => {
    const base = rec({ output: 'abc', endOffset: 3 })
    const r = mergeOutput(base, 1, 5, 'bcde')
    expect(r.output).toBe('abcde')
    expect(r.endOffset).toBe(5)
  })
  it('flags hydration on a gap instead of concatenating', () => {
    const base = rec({ output: 'abc', endOffset: 3 })
    const r = mergeOutput(base, 5, 8, 'fgh')
    expect(r.output).toBe('abc')
    expect(r.needsHydration).toBe(true)
  })
  it('handles multibyte byte offsets (é = 2 bytes)', () => {
    // 'é' is 2 UTF-8 bytes; offset 0..2 covers it. A following chunk at byte 2.
    expect(utf8Len('é')).toBe(2)
    const first = mergeOutput(rec(), 0, 2, 'é')
    expect(first.endOffset).toBe(2)
    // Overlapping chunk re-sends 'é' then adds 'x': suffix must be just 'x'.
    const r = mergeOutput(first, 0, 3, 'éx')
    expect(r.output).toBe('éx')
    expect(r.endOffset).toBe(3)
  })
})

describe('upsertTerminalState', () => {
  it('adds a record with stable order', () => {
    let col = emptyTerminals()
    col = upsertTerminalState(col, meta('a'))
    col = upsertTerminalState(col, meta('b'))
    expect(col.order).toEqual(['a', 'b'])
  })
  it('does not regress output offsets when a stale snapshot replays', () => {
    let col = emptyTerminals()
    col = upsertTerminalState(col, meta('a'))
    col = applyTerminalOutput(col, out('a', 0, 3, 'abc'))
    col = upsertTerminalState(col, meta('a', { state: 'running', output_offset: 0 }))
    expect(col.records.a.output).toBe('abc')
    expect(col.records.a.endOffset).toBe(3)
  })
  it('records exit state', () => {
    let col = emptyTerminals()
    col = upsertTerminalState(col, meta('a', { state: 'exited', exit_code: 1, exit_reason: 'x' }))
    expect(col.records.a).toMatchObject({ state: 'exited', exitCode: 1, exitReason: 'x' })
  })
  it('flags hydration for a newly-seen terminal with pre-existing output', () => {
    const col = upsertTerminalState(emptyTerminals(), meta('a', { output_offset: 10 }))
    expect(col.records.a.needsHydration).toBe(true)
  })
})

describe('applyTerminalBuffer', () => {
  it('replaces a dropped buffer with the authoritative snapshot', () => {
    let col = applyTerminalOutput(emptyTerminals(), out('a', 0, 3, 'abc'))
    col = applyTerminalBuffer(col, 'a', { text: 'XYZ', start_offset: 3, end_offset: 6, dropped: true })
    expect(col.records.a.output).toBe('XYZ')
    expect(col.records.a.endOffset).toBe(6)
    expect(col.records.a.needsHydration).toBe(false)
  })
  it('clears hydration flags when the buffer is already surpassed (REST/WS race)', () => {
    let col = applyTerminalOutput(emptyTerminals(), out('a', 0, 5, 'abcde'))
    col = applyTerminalBuffer(col, 'a', { text: 'abc', start_offset: 0, end_offset: 3, dropped: false })
    expect(col.records.a.output).toBe('abcde')
    expect(col.records.a.needsHydration).toBe(false)
  })
})
