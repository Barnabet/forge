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

  it('finished with images keeps them on the tool card', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'view', display: 'out.pdf' }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'view', output: 'Rendered 2 page(s).', images: ['data:image/png;base64,AAAA', 'data:image/png;base64,BBBB'] }),
    ])
    expect(s.items[0]).toMatchObject({
      kind: 'tool', status: 'done', images: ['data:image/png;base64,AAAA', 'data:image/png;base64,BBBB'],
    })
  })

  it('finished with diff stats keeps them for the inline diff', () => {
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

describe('reducer: plan mode & todos (v1.2)', () => {
  it('mode_changed updates mode', () => {
    seq = 0
    const s = run([ev('mode_changed', { mode: 'plan' })])
    expect(s.mode).toBe('plan')
    expect(s.items).toHaveLength(0)
  })

  it('todos_updated replaces the snapshot wholesale', () => {
    seq = 0
    const s = run([
      ev('todos_updated', { todos: [{ text: 'a', status: 'in_progress' }] }),
      ev('todos_updated', { todos: [
        { text: 'a', status: 'completed' }, { text: 'b', status: 'pending' }] }),
    ])
    expect(s.todos).toEqual([
      { text: 'a', status: 'completed' }, { text: 'b', status: 'pending' }])
  })

  it('plan_proposed pushes a pending card, replacing the pending tool line', () => {
    seq = 0
    const s = run([
      eph('tool_call_pending', { call_id: 'p1', tool: 'propose_plan' }),
      ev('plan_proposed', { call_id: 'p1', plan: '# Plan' }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'plan', callId: 'p1', plan: '# Plan', state: 'pending' })
  })

  it('approve marks the card approved and swallows the tool result', () => {
    seq = 0
    const s = run([
      ev('plan_proposed', { call_id: 'p1', plan: '# Plan' }),
      ev('plan_resolved', { call_id: 'p1', decision: 'approve' }),
      ev('mode_changed', { mode: 'act' }),
      ev('tool_call_finished', { call_id: 'p1', tool: 'propose_plan', output: 'Plan approved.' }),
    ])
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'plan', state: 'approved' })
    expect(s.mode).toBe('act')
  })

  it('revise keeps the card with feedback; a new proposal adds a fresh card', () => {
    seq = 0
    const s = run([
      ev('plan_proposed', { call_id: 'p1', plan: 'v1' }),
      ev('plan_resolved', { call_id: 'p1', decision: 'revise', feedback: 'more tests' }),
      ev('tool_call_finished', { call_id: 'p1', tool: 'propose_plan', output: 'User requested changes' }),
      ev('plan_proposed', { call_id: 'p2', plan: 'v2' }),
    ])
    expect(s.items).toHaveLength(2)
    expect(s.items[0]).toMatchObject({ kind: 'plan', state: 'revising', feedback: 'more tests' })
    expect(s.items[1]).toMatchObject({ kind: 'plan', callId: 'p2', state: 'pending' })
  })

  it('a dangling close removes a still-pending card and keeps the error line', () => {
    seq = 0
    const s = run([
      ev('plan_proposed', { call_id: 'p1', plan: 'v1' }),
      ev('tool_call_finished', {
        call_id: 'p1', tool: 'propose_plan',
        output: '[Cancelled by user — no result]', is_error: true }),
    ])
    expect(s.items.filter(it => it.kind === 'plan')).toHaveLength(0)
    expect(s.items).toHaveLength(1)
    expect(s.items[0]).toMatchObject({ kind: 'tool', status: 'error' })
  })

  it('session meta seeds are overridden by later events on replay', () => {
    seq = 0
    const s = run([
      ev('mode_changed', { mode: 'plan' }),
      ev('mode_changed', { mode: 'act' }),
    ])
    expect(s.mode).toBe('act')
  })
})

describe('reducer: thinking interval', () => {
  it('starts null and anchors on status_changed→running (ms from ts seconds)', () => {
    seq = 0
    expect(emptyStream().thinkingSince).toBeNull()
    // ts is epoch seconds; anchor must be ms
    const s = run([
      { type: 'status_changed', session_id: 's1', seq: 1, ts: 1700, status: 'running' } as unknown as WireEvent,
    ])
    expect(s.thinkingSince).toBe(1_700_000)
  })

  it('clears the anchor when status leaves running', () => {
    seq = 0
    const s = run([
      { type: 'status_changed', session_id: 's1', seq: 1, ts: 1700, status: 'running' } as unknown as WireEvent,
      { type: 'status_changed', session_id: 's1', seq: 2, ts: 1701, status: 'idle' } as unknown as WireEvent,
    ])
    expect(s.thinkingSince).toBeNull()
  })

  it('does not anchor when a tool is visibly running under status_changed', () => {
    seq = 0
    const s = run([
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'ls' }, { seq: 1 }),
      { type: 'status_changed', session_id: 's1', seq: 2, ts: 1700, status: 'running' } as unknown as WireEvent,
    ])
    expect(s.thinkingSince).toBeNull()
  })

  it('clears on tool start/text/assistant and restarts after a lone tool finishes mid-run', () => {
    seq = 0
    const s = run([
      { type: 'status_changed', session_id: 's1', seq: 1, ts: 1700, status: 'running' } as unknown as WireEvent,
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'ls' }, { seq: 2 }),
    ])
    expect(s.thinkingSince).toBeNull() // tool visibly running hides Thinking
    const s2 = run([
      { type: 'tool_call_finished', session_id: 's1', seq: 3, ts: 1710,
        call_id: 'c1', tool: 'bash', output: 'ok' } as unknown as WireEvent,
    ], s)
    expect(s2.thinkingSince).toBe(1_710_000) // back to silence: fresh anchor
  })

  it('a finished tool while another still runs keeps Thinking hidden', () => {
    seq = 0
    const s = run([
      { type: 'status_changed', session_id: 's1', seq: 1, ts: 1700, status: 'running' } as unknown as WireEvent,
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'a' }, { seq: 2 }),
      ev('tool_call_started', { call_id: 'c2', tool: 'bash', display: 'b' }, { seq: 3 }),
      { type: 'tool_call_finished', session_id: 's1', seq: 4, ts: 1710,
        call_id: 'c1', tool: 'bash', output: 'ok' } as unknown as WireEvent,
    ])
    expect(s.thinkingSince).toBeNull()
  })

  it('clears on run_finished and error', () => {
    seq = 0
    const s = run([
      { type: 'status_changed', session_id: 's1', seq: 1, ts: 1700, status: 'running' } as unknown as WireEvent,
      ev('run_finished', { reason: 'completed' }, { seq: 2 }),
    ])
    expect(s.thinkingSince).toBeNull()
  })

  it('an approved plan tool completion leaves no stale anchor', () => {
    seq = 0
    const s = run([
      { type: 'status_changed', session_id: 's1', seq: 1, ts: 1700, status: 'running' } as unknown as WireEvent,
      ev('plan_proposed', { call_id: 'p1', plan: '# Plan' }, { seq: 2 }),
      ev('plan_resolved', { call_id: 'p1', decision: 'approve' }, { seq: 3 }),
      { type: 'tool_call_finished', session_id: 's1', seq: 4, ts: 1710,
        call_id: 'p1', tool: 'propose_plan', output: 'Plan approved.' } as unknown as WireEvent,
    ])
    // The plan card early-breaks; Thinking is visible again (no live content),
    // so a fresh anchor is correct rather than a stale one.
    expect(s.thinkingSince).toBe(1_710_000)
  })
})

describe('reducer: message_checkpointed', () => {
  it('attaches the checkpoint to an already-published bubble', () => {
    seq = 0
    let s = run([ev('user_message', { text: 'hi' }, { seq: 1 })])
    expect((s.items[0] as { checkpoint?: string | null }).checkpoint).toBeNull()
    s = reduce(s, ev('message_checkpointed', { user_seq: 1, checkpoint: 'cp1' }, { seq: 2 }))
    expect((s.items[0] as { checkpoint?: string | null }).checkpoint).toBe('cp1')
  })
})

describe('reducer: rewind', () => {
  it('truncates active items, keeps the monotonic cursor, and clears run state', () => {
    seq = 0
    let s = run([
      ev('user_message', { text: 'keep', workspace_checkpoint: 'cp1' }, { seq: 1 }),
      ev('assistant_message', { text: 'kept reply', usage_tokens: 100 }, { seq: 2 }),
      ev('user_message', { text: 'remove', workspace_checkpoint: 'cp2' }, { seq: 3 }),
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'x' }, { seq: 4 }),
      eph('text_delta', { text: 'live' }),
    ])
    s = { ...s, todos: [{ text: 'x', status: 'in_progress' }], memoryState: 'running',
      thinkingSince: 123, usageTokens: 100 }
    s = reduce(s, ev('history_rewound', {
      target_user_seq: 3, target_checkpoint: 'cp2', safety_checkpoint: 'cp3',
      replacement: false,
    }, { seq: 9 }))
    expect(s.items.map(it => [it.kind, it.seq])).toEqual([
      ['user', 1], ['prose', 2],
    ])
    expect(s.lastSeq).toBe(9)
    expect(s).toMatchObject({ steps: 0, subagents: null, todos: [], memoryState: null,
      thinkingSince: null, usageTokens: 0 })
  })

  it('clears the compacting flag', () => {
    seq = 0
    let s = run([ev('user_message', { text: 'x', workspace_checkpoint: 'cp1' }, { seq: 1 })])
    s = { ...s, compacting: true }
    s = reduce(s, ev('history_rewound', {
      target_user_seq: 1, target_checkpoint: 'cp1', safety_checkpoint: 'cp2',
      replacement: false,
    }, { seq: 9 }))
    expect(s.compacting).toBe(false)
  })
})

describe('reducer: compaction indicator', () => {
  it('toggles compacting on running and off on done', () => {
    let s = emptyStream()
    expect(s.compacting).toBe(false)
    s = reduce(s, eph('compaction', { state: 'running' }))
    expect(s.compacting).toBe(true)
    s = reduce(s, eph('compaction', { state: 'done' }))
    expect(s.compacting).toBe(false)
  })

  it('tracks section phase/label and resets them on done', () => {
    let s = emptyStream()
    s = reduce(s, eph('compaction', { state: 'running', phase: 3, label: 'Files and Code Sections' }))
    expect(s.compactionPhase).toBe(3)
    expect(s.compactionLabel).toBe('Files and Code Sections')
    s = reduce(s, eph('compaction', { state: 'done' }))
    expect(s.compactionPhase).toBe(0)
    expect(s.compactionLabel).toBe('')
  })
})

describe('reducer: subagents', () => {
  const upd = (fields: object) =>
    eph('subagent_update', { call_id: 'sp1', worker: 1, task: 't1', mode: 'read', ...fields })

  it('upserts workers through the lifecycle and accumulates activity', () => {
    seq = 0
    const s = run([
      upd({ state: 'queued' }),
      upd({ state: 'queued', worker: 2, task: 't2', mode: 'write' }),
      upd({ state: 'running' }),
      upd({ state: 'running', activity: 'grep · foo' }),
      upd({ state: 'running', activity: 'read_file · bar.py' }),
      upd({ state: 'done', report: 'all good' }),
    ])
    expect(s.subagents?.callId).toBe('sp1')
    expect(s.subagents?.workers).toHaveLength(2)
    expect(s.subagents?.workers[0]).toMatchObject({
      worker: 1, state: 'done', report: 'all good',
      activity: ['grep · foo', 'read_file · bar.py'], activityCount: 2,
    })
    expect(s.subagents?.workers[1]).toMatchObject({ worker: 2, mode: 'write', state: 'queued' })
  })

  it('a new spawn call replaces the previous crew', () => {
    seq = 0
    const s = run([
      upd({ state: 'queued' }),
      upd({ state: 'done', report: 'r1' }),
      eph('subagent_update', { call_id: 'sp2', worker: 1, task: 'next', mode: 'read', state: 'queued' }),
    ])
    expect(s.subagents?.callId).toBe('sp2')
    expect(s.subagents?.workers).toHaveLength(1)
    expect(s.subagents?.workers[0]).toMatchObject({ task: 'next', state: 'queued' })
  })

  it('a steering message keeps the live crew and ghosts the bubble', () => {
    seq = 0
    const mid = run([upd({ state: 'running' })])
    const s = run([ev('user_message', { text: 'also do X', steering: true })], mid)
    expect(s.subagents?.workers[0].state).toBe('running')
    expect(s.items[0]).toMatchObject({ kind: 'user', text: 'also do X', pending: true })
  })

  it('assistant_message un-ghosts steering it consumed via context_seq', () => {
    seq = 0
    const mid = run([ev('user_message', { text: 'steer', steering: true })])
    expect(mid.items[0]).toMatchObject({ pending: true })
    const before = run([ev('assistant_message', { text: 'ok', tool_calls: [], context_seq: 0 })], mid)
    expect(before.items[0]).toMatchObject({ pending: true })
    const after = run([ev('assistant_message', { text: 'on it', tool_calls: [], context_seq: 1 })], mid)
    expect(after.items[0]).toMatchObject({ pending: false })
  })

  it('steering_consumed un-ghosts immediately, before the reply arrives', () => {
    seq = 0
    const mid = run([ev('user_message', { text: 'steer', steering: true })])
    expect(mid.items[0]).toMatchObject({ pending: true })
    // The next completion's request goes out; the reply hasn't landed yet.
    const s = run([eph('steering_consumed', { context_seq: 1 })], mid)
    expect(s.items[0]).toMatchObject({ kind: 'user', text: 'steer', pending: false })
  })

  it('steering_consumed relocates past the batch it interrupted', () => {
    seq = 0
    const s = run([
      ev('assistant_message', { text: '', tool_calls: [
        { id: 'c1', name: 'bash', arguments: '{}' },
        { id: 'c2', name: 'bash', arguments: '{}' },
      ] }),
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'sleep' }),
      ev('user_message', { text: 'also do X', steering: true }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: 'ok' }),
      ev('tool_call_started', { call_id: 'c2', tool: 'bash', display: 'ls' }),
      ev('tool_call_finished', { call_id: 'c2', tool: 'bash', output: 'ok' }),
      eph('steering_consumed', { context_seq: 6 }),
    ])
    expect(s.items.map(it => it.kind)).toEqual(['tool', 'tool', 'user'])
    expect(s.items[2]).toMatchObject({ kind: 'user', text: 'also do X', pending: false })
  })

  it('un-ghosting moves the steering bubble past the batch it interrupted', () => {
    seq = 0
    // Steering lands mid-batch: c1 running, then c2 starts and finishes after
    // it. The model only sees the message after both results, so the bubble
    // must end up after both tool lines, before the reply.
    const s = run([
      ev('assistant_message', { text: '', tool_calls: [
        { id: 'c1', name: 'bash', arguments: '{}' },
        { id: 'c2', name: 'bash', arguments: '{}' },
      ] }),
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'sleep' }),
      ev('user_message', { text: 'also do X', steering: true }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: 'ok' }),
      ev('tool_call_started', { call_id: 'c2', tool: 'bash', display: 'ls' }),
      ev('tool_call_finished', { call_id: 'c2', tool: 'bash', output: 'ok' }),
      ev('assistant_message', { text: 'on it', tool_calls: [], context_seq: 6 }),
    ])
    expect(s.items.map(it => it.kind)).toEqual(['tool', 'tool', 'user', 'prose'])
    expect(s.items[2]).toMatchObject({ kind: 'user', text: 'also do X', pending: false })
  })

  it('a moved steering bubble stops before a later (non-consumed) user message', () => {
    seq = 0
    const s = run([
      ev('assistant_message', { text: '', tool_calls: [
        { id: 'c1', name: 'bash', arguments: '{}' },
      ] }),
      ev('tool_call_started', { call_id: 'c1', tool: 'bash', display: 'sleep' }),
      ev('user_message', { text: 'first steer', steering: true }),
      ev('tool_call_finished', { call_id: 'c1', tool: 'bash', output: 'ok' }),
      // consumed by the same completion; must keep send order
      ev('user_message', { text: 'second steer', steering: true }),
      ev('assistant_message', { text: 'on it', tool_calls: [], context_seq: 5 }),
    ])
    expect(s.items.map(it => it.kind)).toEqual(['tool', 'user', 'user', 'prose'])
    expect(s.items[1]).toMatchObject({ text: 'first steer', pending: false })
    expect(s.items[2]).toMatchObject({ text: 'second steer', pending: false })
  })

  it('a new user message clears the crew; run_finished closes stragglers', () => {
    seq = 0
    const mid = run([
      upd({ state: 'running' }),
      ev('run_finished', { reason: 'cancelled' }),
    ])
    expect(mid.subagents?.workers[0].state).toBe('error')
    const s = run([ev('user_message', { text: 'again' })], mid)
    expect(s.subagents).toBeNull()
  })

  it('a new non-steering user message clears a durable-only crew too', () => {
    seq = 0
    const mid = run([
      ev('subagent_state', { call_id: 'sp1', worker: 1, task: 't1', mode: 'read', state: 'done', report: 'r' }),
    ])
    expect(mid.subagents?.workers[0].state).toBe('done')
    const s = run([ev('user_message', { text: 'again' })], mid)
    expect(s.subagents).toBeNull()
  })
})

describe('reducer: durable subagent_state', () => {
  const st = (fields: object) =>
    ev('subagent_state', { call_id: 'sp1', worker: 1, task: 't1', mode: 'read', ...fields })
  const upd = (fields: object) =>
    eph('subagent_update', { call_id: 'sp1', worker: 1, task: 't1', mode: 'read', ...fields })

  it('reconstructs a completed crew from durable states alone (refresh replay)', () => {
    seq = 0
    const s = run([
      st({ state: 'queued' }),
      st({ worker: 2, task: 't2', mode: 'write', state: 'queued' }),
      st({ state: 'running' }),
      st({ state: 'done', report: 'all good' }),
      st({ worker: 2, task: 't2', mode: 'write', state: 'error', report: 'boom' }),
    ])
    expect(s.subagents?.callId).toBe('sp1')
    expect(s.subagents?.workers).toHaveLength(2)
    expect(s.subagents?.workers[0]).toMatchObject({
      worker: 1, task: 't1', mode: 'read', state: 'done', report: 'all good',
      activity: [], activityCount: 0,
    })
    expect(s.subagents?.workers[1]).toMatchObject({
      worker: 2, task: 't2', mode: 'write', state: 'error', report: 'boom',
    })
    // Durable states carry no activity feed.
    expect(s.subagents?.lastActivity).toBeNull()
  })

  it('a durable terminal state preserves activity collected by ephemeral updates', () => {
    seq = 0
    const s = run([
      st({ state: 'running' }),
      upd({ state: 'running', activity: 'grep · foo' }),
      upd({ state: 'running', activity: 'read_file · bar.py' }),
      st({ state: 'done', report: 'done via durable' }),
    ])
    expect(s.subagents?.workers[0]).toMatchObject({
      state: 'done', report: 'done via durable',
      activity: ['grep · foo', 'read_file · bar.py'], activityCount: 2,
    })
  })

  it('a durable state upsert keeps a report already recorded when none is sent', () => {
    seq = 0
    const s = run([
      st({ state: 'done', report: 'first report' }),
      st({ state: 'done' }),
    ])
    expect(s.subagents?.workers[0].report).toBe('first report')
  })

  it('a running activity tick does not regress a durable terminal state', () => {
    seq = 0
    const s = run([
      st({ state: 'done', report: 'settled' }),
      upd({ state: 'running', activity: 'straggler · tick' }),
    ])
    expect(s.subagents?.workers[0]).toMatchObject({
      state: 'done', report: 'settled', activity: ['straggler · tick'],
    })
  })

  it('a different call_id replaces the durable crew', () => {
    seq = 0
    const s = run([
      st({ state: 'done', report: 'r1' }),
      ev('subagent_state', { call_id: 'sp2', worker: 1, task: 'next', mode: 'read', state: 'running' }),
    ])
    expect(s.subagents?.callId).toBe('sp2')
    expect(s.subagents?.workers).toHaveLength(1)
    expect(s.subagents?.workers[0]).toMatchObject({ task: 'next', state: 'running' })
  })
})

describe('reducer: session pill (unread/lastRunReason)', () => {
  it('completed run_finished with unread sets pill state and lastRunSeq', () => {
    seq = 0
    const s = run([
      ev('status_changed', { status: 'running' }, { seq: 1 }),
      ev('run_finished', { reason: 'completed', unread: true }, { seq: 2 }),
    ])
    expect(s).toMatchObject({ lastRunReason: 'completed', unread: true, lastRunSeq: 2 })
  })

  it('completed run_finished without unread records reason but leaves unread false', () => {
    seq = 0
    const s = run([ev('run_finished', { reason: 'completed' }, { seq: 5 })])
    expect(s).toMatchObject({ lastRunReason: 'completed', unread: false, lastRunSeq: 5 })
  })

  it('non-success run_finished clears unread and records the reason', () => {
    let s = run([ev('run_finished', { reason: 'completed', unread: true }, { seq: 2 })])
    s = reduce(s, ev('run_finished', { reason: 'cancelled' }, { seq: 3 }))
    expect(s).toMatchObject({ lastRunReason: 'cancelled', unread: false })
  })

  it('run_acknowledged clears unread only for the matching run_seq', () => {
    let s = run([ev('run_finished', { reason: 'completed', unread: true }, { seq: 2 })])
    s = reduce(s, ev('run_acknowledged', { run_seq: 1 }, { seq: 3 }))
    expect(s.unread).toBe(true) // stale ack for a different run is ignored
    s = reduce(s, ev('run_acknowledged', { run_seq: 2 }, { seq: 4 }))
    expect(s.unread).toBe(false)
  })

  it('history_rewound drops stale pill state', () => {
    let s = run([
      ev('user_message', { text: 'hi', workspace_checkpoint: 'cp1' }, { seq: 1 }),
      ev('run_finished', { reason: 'completed', unread: true }, { seq: 2 }),
      ev('user_message', { text: 'more', workspace_checkpoint: 'cp2' }, { seq: 3 }),
    ])
    s = reduce(s, ev('history_rewound', {
      target_user_seq: 3, target_checkpoint: 'cp2', safety_checkpoint: 'cp4', replacement: false,
    }, { seq: 9 }))
    expect(s).toMatchObject({ lastRunReason: null, unread: false, lastRunSeq: 0 })
  })
})
