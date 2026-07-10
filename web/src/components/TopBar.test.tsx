import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import TopBar from './TopBar'

const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  const { applyEvent } = useForge.getState()
  applyEvent(ev('session_created', 'aa', 1, { name: 'fix the bug', cwd: '/Users/louis/mygent', model: 'm', autonomy: 'yolo' }))
  applyEvent(ev('session_created', 'bb', 1, { name: 'write docs', cwd: '/w', model: 'm', autonomy: 'yolo' }))
  applyEvent(ev('status_changed', 'bb', 2, { status: 'queued' }))
})

describe('TopBar', () => {
  it('shows the queue pill count and the active cwd abbreviated', () => {
    render(<TopBar />)
    expect(screen.getByText('1 queued')).toBeInTheDocument()
    expect(screen.getByText('~/mygent')).toBeInTheDocument()
  })

  it('hides the queue pill when nothing is queued', () => {
    useForge.getState().applyEvent(ev('status_changed', 'bb', 3, { status: 'idle' }))
    render(<TopBar />)
    expect(screen.queryByText(/queued/)).not.toBeInTheDocument()
  })

  it('renders no session tabs', () => {
    render(<TopBar />)
    expect(screen.queryAllByRole('tab')).toHaveLength(0)
  })
})
