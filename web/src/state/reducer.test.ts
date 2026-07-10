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

describe('reducer: streaming text (contract #4)', () => {
  it('deltas accumulate, final assistant_message replaces them', () => {
    seq = 0
    const s = run([
      eph('text_delta', { text: 'Wor' }),
      eph('text_delta', { text: 'king…' }),
      ev('assistant_message', { text: 'Working on it, done.', tool_calls: [] }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'prose', text: 'Working on it, done.', streaming: false })
  })

  it('deltas followed by a tool-only final leave no empty prose', () => {
    seq = 0
    const s = run([
      eph('text_delta', { text: 'hmm' }),
      ev('assistant_message', { text: '', tool_calls: [{ id: 'c1', name: 'bash', arguments: '{}' }] }),
    ])
    expect(s.items).toHaveLength(0)
  })

  it('a new turn starts a new prose item', () => {
    seq = 0
    const s = run([
      ev('assistant_message', { text: 'first', tool_calls: [] }),
      eph('text_delta', { text: 'second…' }),
    ])
    expect(s.items).toHaveLength(2)
    expect(s.items[1]).toMatchObject({ kind: 'prose', text: 'second…', streaming: true })
  })
})

describe('reducer: approvals', () => {
  it('requested → allow: gate disappears, tool card follows', () => {
    seq = 0
    const s = run([
      ev('approval_requested', { call_id: 'c1', tool: 'bash', display: 'rm -rf build' }),
      ev('approval_resolved', { call_id: 'c1', decision: 'allow' }),
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'rm -rf build' }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: 'ok' }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'tool', status: 'done' })
  })

  it('requested → deny: gate stays denied, denial result event is suppressed', () => {
    seq = 0
    const s = run([
      ev('approval_requested', { call_id: 'c1', tool: 'bash', display: 'rm -rf /' }),
      ev('approval_resolved', { call_id: 'c1', decision: 'deny' }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: 'User denied this action.', is_error: true }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'gate', denied: true, display: 'rm -rf /' })
  })
})

describe('reducer: run lifecycle', () => {
  it('run_finished(completed) sets idle silently', () => {
    seq = 0
    const s = run([
      ev('status_changed', { status: 'running' }),
      ev('run_finished', { reason: 'completed' }),
    ])
    expect(s.status).toBe('idle')
    expect(s.items).toHaveLength(0)
  })

  it('run_finished(interrupted) sets idle and notes it (contract #3)', () => {
    seq = 0
    const s = run([
      ev('status_changed', { status: 'running' }),
      ev('run_finished', { reason: 'interrupted' }),
    ])
    expect(s.status).toBe('idle')
    expect(s.items[0]).toMatchObject({ kind: 'info', text: 'Interrupted by server restart' })
  })

  it('cancelled adds an info line; error events render', () => {
    seq = 0
    const s = run([
      ev('error', { message: 'LLM connection failed' }),
      ev('run_finished', { reason: 'error' }),
      ev('run_finished', { reason: 'cancelled' }),
    ])
    expect(s.items[0]).toMatchObject({ kind: 'error', message: 'LLM connection failed' })
    expect(s.items[1]).toMatchObject({ kind: 'info', text: 'Run cancelled' })
  })

  it('context_compacted adds a divider; steps count tool calls per turn', () => {
    seq = 0
    const s = run([
      ev('user_message', { text: 'go' }),
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'a' }),
      ev('tool_call_started', { call_id: 'c2', tool: 'bash', display: 'b' }),
      ev('context_compacted', { summary: 'sum', upto_seq: 3 }),
      ev('user_message', { text: 'more' }),
    ])
    expect(s.items.map(i => i.kind)).toEqual(['user', 'tool', 'tool', 'compacted', 'user'])
    expect(s.steps).toBe(0) // reset by the second user message
  })
})

describe('reducer: v1.1 events', () => {
  it('session_created carries project and effort', () => {
    seq = 0
    const s = run([ev('session_created', { name: 'n', cwd: '/w', model: 'm',
      autonomy: 'yolo', project_id: 'p1', effort: 'high' })])
    expect(s).toMatchObject({ projectId: 'p1', effort: 'high', archived: false })
  })

  it('v1 session_created without new fields defaults them', () => {
    seq = 0
    const s = run([ev('session_created', { name: 'n', cwd: '/w', model: 'm', autonomy: 'yolo' })])
    expect(s).toMatchObject({ projectId: null, effort: 'default', archived: false })
  })

  it('archive/unarchive flip the flag; effort_changed updates', () => {
    seq = 0
    const s = run([
      ev('session_created', { name: 'n', cwd: '/w', model: 'm', autonomy: 'yolo' }),
      ev('session_archived', {}),
      ev('effort_changed', { effort: 'low' }),
    ])
    expect(s).toMatchObject({ archived: true, effort: 'low' })
    const s2 = run([ev('session_unarchived', {}, { seq: 4 })], s)
    expect(s2.archived).toBe(false)
  })
})

describe('reducer: context usage', () => {
  it('tracks the latest nonzero usage_tokens from assistant messages', () => {
    seq = 0
    const s = run([
      ev('assistant_message', { text: 'a', tool_calls: [], usage_tokens: 1200 }),
      ev('assistant_message', { text: 'b', tool_calls: [], usage_tokens: 3400 }),
    ])
    expect(s.usageTokens).toBe(3400)
  })

  it('keeps the last known usage when an old event carries none', () => {
    seq = 0
    const s = run([
      ev('assistant_message', { text: 'a', tool_calls: [], usage_tokens: 1200 }),
      ev('assistant_message', { text: 'b', tool_calls: [] }),  // V1-era event
    ])
    expect(s.usageTokens).toBe(1200)
  })
})

describe('reducer: pending tool calls (live stream announcements)', () => {
  it('pending pushes a running placeholder and counts the step', () => {
    seq = 0
    const s = run([eph('tool_call_pending', { call_id: 'c1', tool: 'edit_file' })])
    expect(s.items[0]).toMatchObject({
      kind: 'tool', callId: 'c1', tool: 'edit_file', display: '',
      status: 'running', pending: true,
    })
    expect(s.steps).toBe(1)
  })

  it('ignores a duplicate announcement for the same call', () => {
    seq = 0
    const s = run([
      eph('tool_call_pending', { call_id: 'c1', tool: 'bash' }),
      eph('tool_call_pending', { call_id: 'c1', tool: 'bash' }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.steps).toBe(1)
  })

  it('tool_call_started upgrades the pending line in place, step counted once', () => {
    seq = 0
    const s = run([
      eph('tool_call_pending', { call_id: 'c1', tool: 'edit_file' }),
      ev('tool_call_started', { call_id: 'c1', tool: 'edit_file', display: 'a.py', auto_approved: true }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({
      kind: 'tool', callId: 'c1', display: 'a.py', status: 'running', autoApproved: true,
    })
    expect((s.items[0] as { pending?: boolean }).pending).toBeUndefined()
    expect(s.steps).toBe(1)
  })

  it('approval_requested replaces the pending line with the gate', () => {
    seq = 0
    const s = run([
      eph('tool_call_pending', { call_id: 'c1', tool: 'bash' }),
      ev('approval_requested', { call_id: 'c1', tool: 'bash', display: 'rm -rf' }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'gate', callId: 'c1' })
  })

  it('assistant_message prunes pending lines its tool_calls list dropped', () => {
    seq = 0
    const s = run([
      eph('tool_call_pending', { call_id: 'ghost', tool: 'bash' }),
      eph('tool_call_pending', { call_id: 'kept', tool: 'bash' }),
      ev('assistant_message', {
        text: '', tool_calls: [{ id: 'kept', name: 'bash', arguments: '{}' }],
      }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'tool', callId: 'kept', pending: true })
  })

  it('run_finished and error prune all pending lines', () => {
    seq = 0
    const s = run([
      eph('tool_call_pending', { call_id: 'c1', tool: 'bash' }),
      ev('error', { message: 'boom' }),
    ])
    expect(s.items.map(i => i.kind)).toEqual(['error'])

    seq = 0
    const s2 = run([
      eph('tool_call_pending', { call_id: 'c1', tool: 'bash' }),
      ev('run_finished', { reason: 'cancelled' }),
    ])
    expect(s2.items.map(i => i.kind)).toEqual(['info'])
  })
})
