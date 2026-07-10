import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import TodoStrip from './TodoStrip'

const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
})

function seed(todos: object[]) {
  const { applyEvent } = useForge.getState()
  applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
  applyEvent(ev('todos_updated', 'aa', 2, { todos }))
}

describe('TodoStrip', () => {
  it('renders nothing without todos', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    const { container } = render(<TodoStrip />)
    expect(container).toBeEmptyDOMElement()
  })

  it('collapsed: shows progress and the in_progress item', () => {
    seed([
      { text: 'done step', status: 'completed' },
      { text: 'current step', status: 'in_progress' },
      { text: 'next step', status: 'pending' },
    ])
    render(<TodoStrip />)
    expect(screen.getByText('◐ 1/3')).toBeInTheDocument()
    expect(screen.getByText('current step')).toBeInTheDocument()
    expect(screen.queryByText('next step')).not.toBeInTheDocument()
  })

  it('expands to the full checklist', async () => {
    seed([
      { text: 'done step', status: 'completed' },
      { text: 'current step', status: 'in_progress' },
      { text: 'next step', status: 'pending' },
    ])
    render(<TodoStrip />)
    await userEvent.click(screen.getByText('◐ 1/3'))
    expect(screen.getByText('done step')).toBeInTheDocument()
    expect(screen.getByText('next step')).toBeInTheDocument()
  })
})
