import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
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
    expect(screen.getByText('Working · step 1')).toBeInTheDocument()

    apply(ev('status_changed', 5, { status: 'attention' }))
    rerender(<ChatStream />)
    expect(screen.getByText('Waiting on approval · step 1')).toBeInTheDocument()

    apply(ev('run_finished', 6, { reason: 'completed' }))
    rerender(<ChatStream />)
    expect(screen.queryByText(/step 1/)).not.toBeInTheDocument()
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
})
