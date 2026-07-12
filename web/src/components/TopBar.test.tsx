import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import TopBar from './TopBar'

const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.setState({
    projects: [{ id: 'p1', name: 'mygent', cwd: '/w', default_model: '',
                 default_autonomy: '', default_effort: '' }],
  })
  const { applyEvent } = useForge.getState()
  applyEvent(ev('session_created', 'aa', 1, { name: 'fix the bug', cwd: '/Users/louis/mygent', model: 'm', autonomy: 'yolo', project_id: 'p1' }))
  applyEvent(ev('session_created', 'bb', 1, { name: 'write docs', cwd: '/w', model: 'm', autonomy: 'yolo' }))
  applyEvent(ev('status_changed', 'bb', 2, { status: 'queued' }))
})

describe('TopBar', () => {
  it('shows the active session project name as context', () => {
    useForge.getState().setActive('aa')
    render(<TopBar />)
    expect(screen.getByText('mygent')).toBeInTheDocument()
  })

  it('shows Ad-hoc for a session without a project', () => {
    useForge.getState().setActive('bb')
    render(<TopBar />)
    expect(screen.getByText('Ad-hoc')).toBeInTheDocument()
  })

  it('toggles the projects menu from the brand', async () => {
    render(<TopBar />)
    expect(screen.queryByText('AD-HOC')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Toggle projects menu' }))
    expect(screen.getByText('AD-HOC')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Toggle projects menu' }))
    expect(screen.queryByText('AD-HOC')).not.toBeInTheDocument()
  })

  it('creates a session in the active project from the + button', async () => {
    useForge.getState().setActive('aa')
    const spy = vi.spyOn(useForge.getState(), 'newSessionInProject').mockResolvedValue()
    render(<TopBar />)
    await userEvent.click(screen.getByRole('button', { name: 'New session in mygent' }))
    expect(spy).toHaveBeenCalledWith('p1')
  })

  it('opens the ad-hoc dialog from + when the session has no project', async () => {
    useForge.getState().setActive('bb')
    const spy = vi.spyOn(useForge.getState(), 'openDialog')
    render(<TopBar />)
    await userEvent.click(screen.getByRole('button', { name: 'New session' }))
    expect(spy).toHaveBeenCalledWith('new-session')
  })

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

  it('shows the memory pill while running and its outcome when done', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('memory_update', 'aa', 0, { state: 'running' }))
    render(<TopBar />)
    expect(screen.getByText('memory…')).toBeInTheDocument()

    act(() => applyEvent(ev('memory_update', 'aa', 0, { state: 'written' })))
    expect(screen.getByText('memory updated')).toBeInTheDocument()
  })

  it('hides the memory pill before any memory pass and clears it on a new message', () => {
    render(<TopBar />)
    expect(screen.queryByText(/memory/)).not.toBeInTheDocument()

    const { applyEvent } = useForge.getState()
    act(() => applyEvent(ev('memory_update', 'aa', 0, { state: 'unchanged' })))
    expect(screen.getByText('memory unchanged')).toBeInTheDocument()

    act(() => applyEvent(ev('user_message', 'aa', 5, { text: 'next task', images: [] })))
    expect(screen.queryByText(/memory/)).not.toBeInTheDocument()
  })
})
