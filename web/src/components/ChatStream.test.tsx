import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import ChatStream from './ChatStream'

const ev = (type: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: 'aa', ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  const { applyEvent } = useForge.getState()
  applyEvent(ev('session_created', 1, { name: 'n', cwd: '/w', model: 'm', autonomy: 'guarded' }))
})

afterEach(() => {
  vi.useRealTimers()
})

const apply = (...events: WireEvent[]) => {
  const { applyEvent } = useForge.getState()
  events.forEach(applyEvent)
}

describe('ChatStream', () => {
  it('renders user bubble, markdown prose, tool card, and gate', () => {
    apply(
      ev('user_message', 2, { text: 'fix the bug' }),
      ev('assistant_message', 3, { text: 'Looking at **app.py** now.', tool_calls: [] }),
      ev('tool_call_started', 4, { call_id: 'c1', tool: 'bash', display: 'pytest -q' }),
      ev('approval_requested', 5, { call_id: 'c2', tool: 'bash', display: 'rm -rf build' }),
    )
    render(<ChatStream />)
    expect(screen.getByText('fix the bug')).toBeInTheDocument()
    expect(screen.getByText('app.py')).toBeInTheDocument()   // <strong> from markdown
    expect(screen.getByText('pytest -q')).toBeInTheDocument()
    expect(screen.getByText('Approval required')).toBeInTheDocument()
  })

  it('shows the status line for running/attention/queued, hides when idle', () => {
    apply(
      ev('user_message', 2, { text: 'go' }),
      ev('status_changed', 3, { status: 'running' }),
      ev('tool_call_started', 4, { call_id: 'c1', tool: 'bash', display: 'ls' }),
    )
    const { rerender } = render(<ChatStream />)
    // a running tool card tells the story; no "Thinking" line on top of it
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument()

    apply(ev('tool_call_finished', 5, { call_id: 'c1', tool: 'bash', output: 'ok', is_error: false }))
    rerender(<ChatStream />)
    expect(screen.getByText('Thinking')).toBeInTheDocument()

    apply(ev('status_changed', 6, { status: 'attention' }))
    rerender(<ChatStream />)
    expect(screen.getByText('Waiting on approval · step 1')).toBeInTheDocument()

    apply(ev('run_finished', 7, { reason: 'completed' }))
    rerender(<ChatStream />)
    expect(screen.queryByText(/step 1/)).not.toBeInTheDocument()
  })

  it('shows the compaction progress at idle and clears it when done', () => {
    const comp = (state: string, phase = 0, label = ''): WireEvent =>
      ({ type: 'compaction', session_id: 'aa', seq: 0, state, phase, label }) as unknown as WireEvent
    // Idle session, manual /compact fires — starts in the Analyzing phase.
    apply(comp('running'))
    const { rerender } = render(<ChatStream />)
    expect(screen.getByText('Compacting context')).toBeInTheDocument()
    expect(screen.getByText('0/9')).toBeInTheDocument()

    // A section header crosses: the label and count advance.
    apply(comp('running', 3, 'Files and Code Sections'))
    rerender(<ChatStream />)
    expect(screen.getByText('Compacting context · Files and Code Sections')).toBeInTheDocument()
    expect(screen.getByText('3/9')).toBeInTheDocument()

    apply(comp('done'))
    rerender(<ChatStream />)
    expect(screen.queryByText(/Compacting context/)).not.toBeInTheDocument()
  })

  it('shows an increasing timer beside "Thinking" and resets it after a real hide', () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    // The anchor rides on the event ts (epoch seconds); elapsed is measured
    // against the wall clock, so keep the two in lockstep.
    apply(ev('status_changed', 2, { status: 'running', ts: 0 }))
    const { rerender } = render(<ChatStream />)

    expect(screen.getByText('Thinking')).toBeInTheDocument()
    expect(screen.getByText('0s')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(screen.getByText('2s')).toBeInTheDocument()

    apply(ev('tool_call_started', 3, { call_id: 'c1', tool: 'bash', display: 'ls', ts: 2 }))
    rerender(<ChatStream />)
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument()
    expect(screen.queryByText('2s')).not.toBeInTheDocument()

    // A real Thinking-hide followed by return to silence re-anchors at "now",
    // so the timer restarts from zero rather than resuming the old span.
    apply(ev('tool_call_finished', 4, { call_id: 'c1', tool: 'bash', output: 'ok', is_error: false, ts: 2 }))
    rerender(<ChatStream />)
    expect(screen.getByText('Thinking')).toBeInTheDocument()
    expect(screen.getByText('0s')).toBeInTheDocument()
  })

  it('keeps each session\'s Thinking timer on its own persisted anchor across navigation', () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    // Session A ('aa') is created in beforeEach; anchor it thinking at t=0.
    apply(ev('status_changed', 2, { status: 'running', ts: 0 }))
    const { rerender } = render(<ChatStream />)
    expect(screen.getByText('0s')).toBeInTheDocument()

    act(() => { vi.advanceTimersByTime(3000) })
    expect(screen.getByText('3s')).toBeInTheDocument()

    // Session B ('bb') starts thinking later, at t=3s.
    apply(
      ev('session_created', 3, { session_id: 'bb', name: 'n2', cwd: '/w', model: 'm', autonomy: 'guarded' }),
      ev('status_changed', 4, { session_id: 'bb', status: 'running', ts: 3 }),
    )
    act(() => { useForge.getState().setActive('bb') })
    rerender(<ChatStream />)
    // B shows its own fresh span, not A's 3s.
    expect(screen.getByText('0s')).toBeInTheDocument()
    expect(screen.queryByText('3s')).not.toBeInTheDocument()

    act(() => { vi.advanceTimersByTime(2000) })
    expect(screen.getByText('2s')).toBeInTheDocument()

    // Back to A: it must reflect its persisted anchor (t=0 → now 5s), not restart.
    act(() => { useForge.getState().setActive('aa') })
    rerender(<ChatStream />)
    expect(screen.getByText('5s')).toBeInTheDocument()
  })

  it('restores the elapsed value from the persisted anchor after a remount', () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    apply(ev('status_changed', 2, { status: 'running', ts: 0 }))
    const { unmount } = render(<ChatStream />)
    expect(screen.getByText('0s')).toBeInTheDocument()

    unmount()
    act(() => { vi.advanceTimersByTime(4000) })

    render(<ChatStream />)
    expect(screen.getByText('4s')).toBeInTheDocument()
  })

  it('hides "Thinking" while text is streaming in', () => {
    apply(
      ev('status_changed', 2, { status: 'running' }),
      ev('text_delta', 0, { text: 'Pondering about ' }),
    )
    render(<ChatStream />)
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument()
  })

  it('hides "Thinking" after final prose while the run lingers (post-turn bookkeeping)', () => {
    apply(
      ev('status_changed', 2, { status: 'running' }),
      ev('assistant_message', 3, { text: 'All done.', tool_calls: [] }),
    )
    render(<ChatStream />)
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument()
  })

  it('renders markdown lists as real list items', () => {
    apply(ev('assistant_message', 2, {
      text: 'The pull brought in:\n\n- New code: `anchor.py`\n- New tests\n- Docs',
      tool_calls: [],
    }))
    render(<ChatStream />)
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
    expect(screen.getByRole('list')).toBeInTheDocument()
  })

  it('renders error, info, and compaction items', () => {
    apply(
      ev('error', 2, { message: 'LLM unreachable' }),
      ev('run_finished', 3, { reason: 'cancelled' }),
      ev('context_compacted', 4, { summary: 's', upto_seq: 2 }),
    )
    render(<ChatStream />)
    expect(screen.getByText('LLM unreachable')).toBeInTheDocument()
    expect(screen.getByText('Run cancelled')).toBeInTheDocument()
    expect(screen.getByText('· context compacted ·')).toBeInTheDocument()
  })

  it('edits a message inline and confirms the workspace-restore before rewinding', async () => {
    const user = userEvent.setup()
    const submitEdit = vi.fn(async () => {})
    useForge.setState({ submitEdit })
    apply(ev('user_message', 2, { text: 'fix the bug', workspace_checkpoint: 'cp1' }))
    render(<ChatStream />)

    // Enter inline edit mode via the pencil.
    await user.click(screen.getByRole('button', { name: 'Edit from here' }))
    const box = screen.getByDisplayValue('fix the bug')
    await user.clear(box)
    await user.type(box, 'fix it properly')

    // Save does NOT rewind yet — it opens the workspace-restore confirmation.
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(submitEdit).not.toHaveBeenCalled()
    expect(screen.getByText('Save edit?')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Save & rewind' }))
    expect(submitEdit).toHaveBeenCalledWith(2, 'fix it properly', [])
  })

  it('cancels an inline edit without rewinding', async () => {
    const user = userEvent.setup()
    const submitEdit = vi.fn(async () => {})
    useForge.setState({ submitEdit })
    apply(ev('user_message', 2, { text: 'fix the bug', workspace_checkpoint: 'cp1' }))
    render(<ChatStream />)

    await user.click(screen.getByRole('button', { name: 'Edit from here' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByDisplayValue('fix the bug')).not.toBeInTheDocument()
    expect(screen.getByText('fix the bug')).toBeInTheDocument()
    expect(submitEdit).not.toHaveBeenCalled()
  })

  it('does not leak dropdown state to the next gate when a gate resolves', () => {
    apply(
      ev('approval_requested', 2, { call_id: 'c1', tool: 'bash', display: 'cmd-a' }),
      ev('approval_requested', 3, { call_id: 'c2', tool: 'bash', display: 'cmd-b' }),
    )
    const { rerender } = render(<ChatStream />)

    // open the FIRST gate's "Always" dropdown
    fireEvent.click(screen.getAllByText('Always ⌄')[0])
    expect(screen.getByText('Always allow this command (session)')).toBeInTheDocument()

    // gate c1 resolves allow (reducer splices it out); its tool call runs
    apply(
      ev('approval_resolved', 4, { call_id: 'c1', decision: 'allow' }),
      ev('tool_call_started', 5, { call_id: 'c1', tool: 'bash', display: 'cmd-a' }),
      ev('tool_call_finished', 6, { call_id: 'c1', tool: 'bash', output: 'ok', is_error: false }),
    )
    rerender(<ChatStream />)

    // surviving gate c2 must NOT inherit c1's open menu
    expect(screen.getByText('cmd-b')).toBeInTheDocument()
    expect(screen.queryByText('Always allow this command (session)')).not.toBeInTheDocument()
    expect(screen.queryByText('Always allow bash (session)')).not.toBeInTheDocument()
    expect(screen.queryByText('Always allow bash (global)')).not.toBeInTheDocument()
  })
})
