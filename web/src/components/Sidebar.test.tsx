import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import Sidebar from './Sidebar'

const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  vi.restoreAllMocks()
  useForge.setState({
    projects: [{ id: 'p1', name: 'mygent', cwd: '/w', default_model: '',
                 default_autonomy: '', default_effort: '' }],
  })
  const { applyEvent } = useForge.getState()
  applyEvent(ev('session_created', 'aa', 1, { name: 'fix bug', cwd: '/w', model: 'm', autonomy: 'yolo', project_id: 'p1' }))
  applyEvent(ev('session_created', 'bb', 1, { name: 'scratch', cwd: '/tmp', model: 'm', autonomy: 'yolo' }))
  applyEvent(ev('session_created', 'cc', 1, { name: 'old work', cwd: '/w', model: 'm', autonomy: 'yolo', project_id: 'p1' }))
  applyEvent(ev('session_archived', 'cc', 2, {}))
})

describe('Sidebar', () => {
  it('groups sessions by project, ad-hoc, archived', () => {
    render(<Sidebar />)
    expect(screen.getByText('mygent')).toBeInTheDocument()
    expect(screen.getByText('fix bug')).toBeInTheDocument()
    expect(screen.getByText('AD-HOC')).toBeInTheDocument()
    expect(screen.getByText('scratch')).toBeInTheDocument()
    expect(screen.getByText('ARCHIVED (1)')).toBeInTheDocument()
    expect(screen.queryByText('old work')).not.toBeInTheDocument()  // collapsed by default
  })

  it('expanding archived shows the row with unarchive and delete', async () => {
    render(<Sidebar />)
    await userEvent.click(screen.getByText('ARCHIVED (1)'))
    expect(screen.getByText('old work')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unarchive old work' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete old work' })).toBeInTheDocument()
  })

  it('clicking a session activates it; collapsing a project hides its rows', async () => {
    render(<Sidebar />)
    await userEvent.click(screen.getByText('fix bug'))
    expect(useForge.getState().activeId).toBe('aa')
    await userEvent.click(screen.getByText('mygent'))  // collapse
    expect(screen.queryByText('fix bug')).not.toBeInTheDocument()
  })

  it('archive action posts; 409 shows a transient inline error', async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, status: 409, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    render(<Sidebar />)
    await userEvent.click(screen.getByRole('button', { name: 'Archive fix bug' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/archive', expect.anything())
    expect(await screen.findByText(/session is running/i)).toBeInTheDocument()
  })

  it('delete requires the confirm dialog', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    render(<Sidebar />)
    await userEvent.click(screen.getByText('ARCHIVED (1)'))
    await userEvent.click(screen.getByRole('button', { name: 'Delete old work' }))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText(/permanently delete/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/cc', expect.objectContaining({ method: 'DELETE' }))
  })

  it('plus rows wire to the right entry points', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: 'ns', name: 'New session', cwd: '/w', model: 'm',
                           autonomy: 'yolo', status: 'idle', project_id: 'p1',
                           archived: false, effort: 'default' }),
    }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    render(<Sidebar />)
    await userEvent.click(screen.getByRole('button', { name: 'New session in mygent' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({
      body: JSON.stringify({ project_id: 'p1' }),
    }))
    await userEvent.click(screen.getByRole('button', { name: 'New ad-hoc session' }))
    expect(useForge.getState().dialog).toBe('new-session')
    await userEvent.click(screen.getByRole('button', { name: 'New project' }))
    expect(useForge.getState().dialog).toBe('new-project')
  })
})
