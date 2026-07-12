import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import ProjectsMenu from './ProjectsMenu'

const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  vi.restoreAllMocks()
  localStorage.clear()
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

describe('ProjectsMenu', () => {
  it('groups sessions by project, ad-hoc, archived', () => {
    render(<ProjectsMenu onClose={vi.fn()} />)
    expect(screen.getByText('mygent')).toBeInTheDocument()
    expect(screen.getByText('fix bug')).toBeInTheDocument()
    expect(screen.getByText('AD-HOC')).toBeInTheDocument()
    expect(screen.getByText('scratch')).toBeInTheDocument()
    expect(screen.getByText('ARCHIVED (1)')).toBeInTheDocument()
    expect(screen.queryByText('old work')).not.toBeInTheDocument()  // collapsed by default
  })

  it('expanding archived shows the row with unarchive and delete', async () => {
    render(<ProjectsMenu onClose={vi.fn()} />)
    await userEvent.click(screen.getByText('ARCHIVED (1)'))
    expect(screen.getByText('old work')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unarchive old work' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete old work' })).toBeInTheDocument()
  })

  it('clicking a session activates it and closes; collapsing a project hides its rows', async () => {
    const onClose = vi.fn()
    render(<ProjectsMenu onClose={onClose} />)
    await userEvent.click(screen.getByText('fix bug'))
    expect(useForge.getState().activeId).toBe('aa')
    expect(onClose).toHaveBeenCalled()
    await userEvent.click(screen.getByText('mygent'))  // collapse
    expect(screen.queryByText('fix bug')).not.toBeInTheDocument()
  })

  it('persists collapsed sections across remounts', async () => {
    const { unmount } = render(<ProjectsMenu onClose={vi.fn()} />)
    expect(screen.getByText('fix bug')).toBeInTheDocument()
    await userEvent.click(screen.getByText('mygent'))  // collapse the project
    expect(screen.queryByText('fix bug')).not.toBeInTheDocument()
    unmount()
    render(<ProjectsMenu onClose={vi.fn()} />)
    expect(screen.queryByText('fix bug')).not.toBeInTheDocument()  // still collapsed
  })

  it('archive action posts; 409 shows a transient inline error', async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, status: 409, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    render(<ProjectsMenu onClose={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Archive fix bug' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/archive', expect.anything())
    expect(await screen.findByText(/session is running/i)).toBeInTheDocument()
  })

  it('delete requires the confirm dialog', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    render(<ProjectsMenu onClose={vi.fn()} />)
    await userEvent.click(screen.getByText('ARCHIVED (1)'))
    await userEvent.click(screen.getByRole('button', { name: 'Delete old work' }))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByText(/permanently delete/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/cc', expect.objectContaining({ method: 'DELETE' }))
  })

  it('confirm dialog auto-dismisses if the session is remotely deleted', async () => {
    const { rerender } = render(<ProjectsMenu onClose={vi.fn()} />)
    await userEvent.click(screen.getByText('ARCHIVED (1)'))
    await userEvent.click(screen.getByRole('button', { name: 'Delete old work' }))
    expect(screen.getByText(/permanently delete/i)).toBeInTheDocument()
    // A remote session_deleted lands while the dialog is open.
    expect(() => {
      useForge.getState().applyEvent(ev('session_deleted', 'cc', 0))
      rerender(<ProjectsMenu onClose={vi.fn()} />)
    }).not.toThrow()
    expect(screen.queryByText(/permanently delete/i)).not.toBeInTheDocument()
  })

  it('orders sessions most-recent-first within a project', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'dd', 1, { name: 'newer work', cwd: '/w', model: 'm', autonomy: 'yolo', project_id: 'p1', ts: 100 }))
    applyEvent(ev('user_message', 'aa', 2, { text: 'hi', ts: 200 }))
    render(<ProjectsMenu onClose={vi.fn()} />)
    const names = screen.getAllByText(/fix bug|newer work/).map(el => el.textContent)
    expect(names).toEqual(['fix bug', 'newer work'])
  })

  it('shows a last-message time label on rows', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('user_message', 'aa', 2, { text: 'hi', ts: Date.now() / 1000 }))
    render(<ProjectsMenu onClose={vi.fn()} />)
    expect(screen.getByText('now')).toBeInTheDocument()
  })

  it('shows a vectorization progress bar while indexing and a check when ready', () => {
    useForge.setState({ fileIndex: { p1: { state: 'indexing', done: 4, total: 10 } } })
    const { rerender } = render(<ProjectsMenu onClose={vi.fn()} />)
    const bar = screen.getByRole('progressbar', { name: 'Vectorizing workspace' })
    expect(bar).toHaveAttribute('aria-valuenow', '40')
    expect(screen.queryByLabelText('Workspace vectorized')).not.toBeInTheDocument()

    useForge.setState({ fileIndex: { p1: { state: 'ready', done: 10, total: 10 } } })
    rerender(<ProjectsMenu onClose={vi.fn()} />)
    expect(screen.queryByRole('progressbar', { name: 'Vectorizing workspace' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Workspace vectorized')).toBeInTheDocument()
  })

  describe('session dot', () => {
    // Patch the 'aa' session stream and return its dot's resolved data-state.
    const dotState = (over: Partial<Record<string, unknown>>, selected = false) => {
      const cur = useForge.getState().sessions.aa
      useForge.setState({
        activeId: selected ? 'aa' : null,
        sessions: { ...useForge.getState().sessions,
                    aa: { ...cur, stream: { ...cur.stream, ...over } } },
      })
      render(<ProjectsMenu onClose={vi.fn()} />)
      const dot = document.querySelector('span[data-state]') as HTMLElement
      return dot.getAttribute('data-state')
    }

    it('idle neutral by default', () => {
      expect(dotState({ status: 'idle', lastRunReason: null, unread: false })).toBe('idle')
    })

    it('selection does not change an idle session dot', () => {
      expect(dotState({ status: 'idle', lastRunReason: null, unread: false }, true)).toBe('idle')
    })

    it('idle error is problem', () => {
      expect(dotState({ status: 'idle', lastRunReason: 'error' })).toBe('problem')
    })

    it('idle interrupted is problem', () => {
      expect(dotState({ status: 'idle', lastRunReason: 'interrupted' })).toBe('problem')
    })

    it('idle unread completion is unread', () => {
      expect(dotState({ status: 'idle', lastRunReason: 'completed', unread: true })).toBe('unread')
    })

    it('user cancellation stays neutral, never red', () => {
      expect(dotState({ status: 'idle', lastRunReason: 'cancelled', unread: false })).toBe('idle')
    })

    it('user cancellation on the selected session stays neutral, never red', () => {
      expect(dotState({ status: 'idle', lastRunReason: 'cancelled', unread: false }, true)).toBe('idle')
    })

    it('running wins over problem', () => {
      expect(dotState({ status: 'running', lastRunReason: 'error' })).toBe('running')
    })

    it('running wins over unread', () => {
      expect(dotState({ status: 'running', lastRunReason: 'completed', unread: true })).toBe('running')
    })

    it('running stays green regardless of selection', () => {
      expect(dotState({ status: 'running', lastRunReason: null, unread: false }, true)).toBe('running')
    })

    it('queued agent is yellow/on hold', () => {
      expect(dotState({ status: 'queued' })).toBe('queued')
    })

    it('attention state is yellow/on hold', () => {
      expect(dotState({ status: 'attention' })).toBe('queued')
    })

    it('queued subagent makes the session yellow/on hold', () => {
      expect(dotState({ status: 'running', subagents: {
        callId: 'c1', lastActivity: null, workers: [{
          worker: 1, task: 'wait', mode: 'read', state: 'queued',
          activity: [], activityCount: 0, report: '',
        }],
      } })).toBe('queued')
    })

    it('blocked subagent makes the session yellow/on hold', () => {
      expect(dotState({ status: 'running', subagents: {
        callId: 'c1', lastActivity: null, workers: [{
          worker: 1, task: 'wait', mode: 'write', state: 'blocked',
          activity: [], activityCount: 0, report: '',
        }],
      } })).toBe('queued')
    })

    it('problem wins over unread', () => {
      expect(dotState({ status: 'idle', lastRunReason: 'error', unread: true })).toBe('problem')
    })

    it('problem is unchanged by selection', () => {
      expect(dotState({ status: 'idle', lastRunReason: 'error' }, true)).toBe('problem')
    })

    it('unread is unchanged by selection', () => {
      expect(dotState({ status: 'idle', lastRunReason: 'completed', unread: true }, true)).toBe('unread')
    })

    it('exposes an accessible label per state', () => {
      const cur = useForge.getState().sessions.aa
      useForge.setState({ sessions: { ...useForge.getState().sessions,
        aa: { ...cur, stream: { ...cur.stream, status: 'idle', lastRunReason: 'error' } } } })
      render(<ProjectsMenu onClose={vi.fn()} />)
      expect(screen.getByLabelText('Last run failed')).toBeInTheDocument()
    })
  })

  it('plus rows wire to the right entry points', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: 'ns', name: 'New session', cwd: '/w', model: 'm',
                           autonomy: 'yolo', status: 'idle', project_id: 'p1',
                           archived: false, effort: 'default' }),
    }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    render(<ProjectsMenu onClose={vi.fn()} />)
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
