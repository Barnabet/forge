import { beforeEach, describe, expect, it } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import SubagentPanel from './SubagentPanel'

const ev = (type: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: 'aa', ts: 0, seq, ...fields }) as unknown as WireEvent
const upd = (fields: object): WireEvent =>
  ({ type: 'subagent_update', session_id: 'aa', seq: 0, call_id: 'sp1',
     worker: 1, task: 'audit the reducer', mode: 'read',
     ...fields }) as unknown as WireEvent
let stateSeq = 1
const st = (fields: object): WireEvent =>
  ({ type: 'subagent_state', session_id: 'aa', ts: 0, seq: ++stateSeq,
     call_id: 'sp1', worker: 1, task: 'audit the reducer', mode: 'read',
     ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent(
    ev('session_created', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
})

const apply = (e: WireEvent) => useForge.getState().applyEvent(e)

describe('SubagentPanel', () => {
  it('renders nothing without a crew', () => {
    const { container } = render(<SubagentPanel />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows count pills and the latest tool line from any worker', () => {
    apply(upd({ state: 'running', activity: 'grep · tool_call_pending' }))
    apply(upd({ state: 'queued', worker: 2, task: 'check the store', mode: 'write' }))
    render(<SubagentPanel />)
    expect(screen.getByText('1 running')).toBeInTheDocument()
    expect(screen.getByText('1 waiting')).toBeInTheDocument()
    expect(screen.getByText('grep · tool_call_pending')).toBeInTheDocument()
    expect(screen.getByText('w1')).toBeInTheDocument()
  })

  it('shows a blocked write worker as waiting its turn', async () => {
    apply(upd({ state: 'running', activity: 'edit_file · config.py' }))
    apply(upd({ state: 'blocked', worker: 2, task: 'check the store', mode: 'write' }))
    render(<SubagentPanel />)
    // Blocked folds into the single waiting pill alongside slot-queued workers.
    expect(screen.getByText('1 running')).toBeInTheDocument()
    expect(screen.getByText('1 waiting')).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Subagents'))
    expect(screen.getByText('waiting its turn…')).toBeInTheDocument()
    expect(screen.queryByText('waiting for a slot…')).not.toBeInTheDocument()
  })

  it('feed follows the newest activity across workers', () => {
    apply(upd({ state: 'running', activity: 'grep · reducer.ts' }))
    apply(upd({ state: 'running', worker: 2, task: 'check the store',
                activity: 'read_file · store.ts' }))
    render(<SubagentPanel />)
    expect(screen.getByText('read_file · store.ts')).toBeInTheDocument()
    expect(screen.getByText('w2')).toBeInTheDocument()
    expect(screen.queryByText('grep · reducer.ts')).not.toBeInTheDocument()
  })

  it('expands to per-worker lanes with live tickers', async () => {
    apply(upd({ state: 'running', activity: 'grep · tool_call_pending' }))
    apply(upd({ state: 'queued', worker: 2, task: 'check the store', mode: 'write' }))
    render(<SubagentPanel />)
    expect(screen.queryByText('audit the reducer')).not.toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Subagents'))
    expect(screen.getByText('audit the reducer')).toBeInTheDocument()
    expect(screen.getByText('check the store')).toBeInTheDocument()
    expect(screen.getByText('waiting for a slot…')).toBeInTheDocument()
    expect(screen.getByText('write')).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Subagents'))
    expect(screen.queryByText('audit the reducer')).not.toBeInTheDocument()
  })

  it('shows how many tool calls precede the latest three ticker lines', async () => {
    for (let i = 1; i <= 6; i++)
      apply(upd({ state: 'running', activity: `tool ${i}` }))
    render(<SubagentPanel />)
    await userEvent.click(screen.getByLabelText('Subagents'))
    expect(screen.getByText('+3 previous tool calls')).toBeInTheDocument()
    expect(screen.queryByText('tool 3')).not.toBeInTheDocument()
    expect(screen.getByText('tool 4')).toBeInTheDocument()
    expect(screen.getByText('tool 5')).toBeInTheDocument()
    expect(screen.getAllByText('tool 6')).toHaveLength(2)
  })

  it('finished worker expands its report on click', async () => {
    apply(upd({ state: 'done', report: 'Found 3 call sites.' }))
    render(<SubagentPanel />)
    expect(screen.getByText('1 done')).toBeInTheDocument()
    expect(screen.getByText('crew finished')).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Subagents'))
    expect(screen.queryByText('Found 3 call sites.')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('audit the reducer'))
    expect(screen.getByText('Found 3 call sites.')).toBeInTheDocument()
  })

  it('failed workers surface in the pills', () => {
    apply(upd({ state: 'error', report: 'Worker failed' }))
    render(<SubagentPanel />)
    expect(screen.getByText('1 failed')).toBeInTheDocument()
    expect(screen.getByText('crew finished with failures')).toBeInTheDocument()
  })

  it('reconstructs the crew from durable subagent_state replay (refresh)', async () => {
    // Simulate a page refresh: only durable states replay, no ephemeral feed.
    apply(st({ state: 'done', report: 'Found 3 call sites.' }))
    apply(st({ worker: 2, task: 'check the store', mode: 'write', state: 'done',
               report: 'Store looks fine.' }))
    render(<SubagentPanel />)
    expect(screen.getByText('2 done')).toBeInTheDocument()
    expect(screen.getByText('crew finished')).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Subagents'))
    expect(screen.getByText('audit the reducer')).toBeInTheDocument()
    expect(screen.getByText('check the store')).toBeInTheDocument()
    expect(screen.queryByText('Found 3 call sites.')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('audit the reducer'))
    expect(screen.getByText('Found 3 call sites.')).toBeInTheDocument()
  })

  it('can be dismissed once all workers have settled', async () => {
    apply(upd({ state: 'running' }))
    const { container } = render(<SubagentPanel />)
    expect(screen.queryByLabelText('Dismiss subagents panel')).not.toBeInTheDocument()
    act(() => apply(upd({ state: 'done', report: 'ok' })))
    await userEvent.click(screen.getByLabelText('Dismiss subagents panel'))
    expect(container).toBeEmptyDOMElement()
  })
})
