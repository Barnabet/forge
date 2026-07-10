import { describe, expect, it } from 'vitest'
import { emptyStream, reduce, type SessionStream } from './reducer'
import type { WireEvent } from '../protocol'

let seq = 0
const ev = (type: string, fields: object = {}, opts: { seq?: number } = {}): WireEvent =>
  ({ type, session_id: 's1', ts: 0, seq: opts.seq ?? ++seq, ...fields }) as unknown as WireEvent
const eph = (type: string, fields: object): WireEvent =>
  ({ type, session_id: 's1', seq: 0, ...fields }) as unknown as WireEvent
const run = (events: WireEvent[], from = emptyStream()): SessionStream =>
  events.reduce(reduce, from)

describe('reducer: session meta', () => {
  it('applies created/renamed/status/autonomy/model', () => {
    seq = 0
    const s = run([
      ev('session_created', { name: 'New session', cwd: '/w', model: 'm1', autonomy: 'yolo' }),
      ev('session_renamed', { name: 'fix the bug' }),
      ev('status_changed', { status: 'running' }),
      ev('autonomy_changed', { autonomy: 'guarded' }),
      ev('model_changed', { model: 'm2' }),
    ])
    expect(s).toMatchObject({
      name: 'fix the bug', cwd: '/w', model: 'm2',
      autonomy: 'guarded', status: 'running', lastSeq: 5,
    })
    expect(s.items).toHaveLength(0)  // meta events produce no stream items
  })
})

describe('reducer: dedupe by seq (replay overlap)', () => {
  it('drops a durable event already applied', () => {
    seq = 0
    const first = ev('user_message', { text: 'hi' })
    const s = run([first, first])  // replayed + live copy
    expect(s.items).toHaveLength(1)
    expect(s.lastSeq).toBe(1)
  })
})

describe('reducer: messages and tool calls', () => {
  it('user message resets steps and appends a bubble', () => {
    seq = 0
    const s = run([ev('user_message', { text: 'do it' })])
    expect(s.items[0]).toMatchObject({ kind: 'user', text: 'do it' })
    expect(s.steps).toBe(0)
  })

  it('assistant text becomes finalized prose', () => {
    seq = 0
    const s = run([ev('assistant_message', { text: 'Working on it.', tool_calls: [] })])
    expect(s.items[0]).toMatchObject({ kind: 'prose', text: 'Working on it.', streaming: false })
  })

  it('assistant message with only tool calls adds no prose', () => {
    seq = 0
    const s = run([ev('assistant_message', { text: '', tool_calls: [{ id: 'c1', name: 'bash', arguments: '{}' }] })])
    expect(s.items).toHaveLength(0)
  })

  it('tool started→chunk→finished lifecycle', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'pytest -q', auto_approved: true }),
      eph('output_chunk', { call_id: 'c1', text: 'collecting…\n' }),
      eph('output_chunk', { call_id: 'c1', text: '3 passed\n' }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: '3 passed', is_error: false, duration_ms: 812, diff_stats: null }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({
      kind: 'tool', callId: 'c1', display: 'pytest -q', status: 'done',
      output: '3 passed', durationMs: 812, autoApproved: true,
    })
    expect(s.steps).toBe(1)
  })

  it('mid-run output chunks accumulate on the running card', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'ls' }),
      eph('output_chunk', { call_id: 'c1', text: 'a\n' }),
      eph('output_chunk', { call_id: 'c1', text: 'b\n' }),
    ])
    expect(s.items[0]).toMatchObject({ kind: 'tool', status: 'running', output: 'a\nb\n' })
  })

  it('finished without started creates a completed card (contract #2)', () => {
    seq = 0
    const s = run([
      ev('tool_call_finished', { call_id: 'cx', tool: 'nope', output: 'Unknown tool: nope', is_error: true, duration_ms: 0, diff_stats: null }),
    ])
    expect(s.items[0]).toMatchObject({
      kind: 'tool', callId: 'cx', display: 'nope', status: 'error', output: 'Unknown tool: nope',
    })
  })

  it('finished with diff stats keeps them for the drawer link', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'edit_file', display: 'app.py' }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'edit_file', output: 'ok', is_error: false, duration_ms: 5, diff_stats: { path: '/w/app.py', added: 41, removed: 38, changeset_index: 2 } }),
    ])
    expect(s.items[0]).toMatchObject({ kind: 'tool', diffStats: { added: 41, removed: 38, changeset_index: 2 } })
  })

  it('optional fields absent (generated types) default safely', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'ls' }),  // no auto_approved
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: 'x' }),   // no is_error/duration/diff
    ])
    expect(s.items[0]).toMatchObject({ autoApproved: false, status: 'done', durationMs: 0, diffStats: null })
  })
})
