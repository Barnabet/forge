import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WorkspaceStatus as WorkspaceStatusData } from '../api'
import WorkspaceStatus from './WorkspaceStatus'

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, message: string) { super(message); this.status = status }
  },
  api: { workspaceStatus: vi.fn() },
}))

import { api } from '../api'

const statusMock = api.workspaceStatus as unknown as ReturnType<typeof vi.fn>

const session = (
  id: string, over: Partial<WorkspaceStatusData['sessions'][number]> = {},
): WorkspaceStatusData['sessions'][number] => ({
  id, name: id, status: 'idle', mode: 'code', archived: false,
  last_message_at: 0, busy: null, ...over,
})

const activity = (
  seq: number, over: Partial<WorkspaceStatusData['recent_activity'][number]> = {},
): WorkspaceStatusData['recent_activity'][number] => ({
  seq, timestamp: Date.now() / 1000, session_id: null, author: 'external',
  origin: 'external', action: 'write_file', paths: ['src/a.ts'], note: null, ...over,
})

const data = (over: Partial<WorkspaceStatusData> = {}): WorkspaceStatusData => ({
  cwd: '/w', sessions: [session('active')], recent_activity: [],
  current_tree: null, reconciled: false, last_external_paths: [], ...over,
})

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.setState({ activeId: 'active' })
  vi.clearAllMocks()
})

describe('WorkspaceStatus', () => {
  it('renders nothing in the solo state with no foreign activity', async () => {
    statusMock.mockResolvedValue(data())
    const { container } = render(<WorkspaceStatus />)
    await waitFor(() => expect(statusMock).toHaveBeenCalled())
    expect(container.firstChild).toBeNull()
  })

  it('shows a pill with session and change counts when peers share the tree', async () => {
    statusMock.mockResolvedValue(data({
      sessions: [session('active'), session('peer1'), session('peer2')],
      recent_activity: [
        activity(2, { session_id: 'peer1', origin: 'fs_api', author: 'session peer1' }),
        activity(1),
      ],
    }))
    render(<WorkspaceStatus />)
    expect(await screen.findByText('3 sessions')).toBeInTheDocument()
    expect(screen.getByText('2 changes')).toBeInTheDocument()
  })

  it('excludes archived and the active session from the peer count', async () => {
    statusMock.mockResolvedValue(data({
      sessions: [session('active'), session('peer1'), session('gone', { archived: true })],
    }))
    render(<WorkspaceStatus />)
    // active + peer1, gone excluded → 2 sessions.
    expect(await screen.findByText('2 sessions')).toBeInTheDocument()
    expect(screen.queryByText(/change/)).not.toBeInTheDocument()
  })

  it('opens a panel listing peers and recent activity with relative paths', async () => {
    statusMock.mockResolvedValue(data({
      sessions: [session('active', { name: 'mine' }), session('peer1', { name: 'docs', busy: true })],
      recent_activity: [
        activity(1, { session_id: 'peer1', origin: 'fs_api', author: 'session peer1', paths: ['src/x.ts'] }),
      ],
    }))
    render(<WorkspaceStatus />)
    const pill = await screen.findByRole('button')
    await userEvent.click(pill)
    expect(screen.getByText('All sessions edit the same live files.')).toBeInTheDocument()
    expect(screen.getByText('docs')).toBeInTheDocument()
    expect(screen.getByText('src/x.ts')).toBeInTheDocument()
    expect(pill).toHaveAttribute('aria-expanded', 'true')
  })

  it('applies external styling and label when external changes exist', async () => {
    statusMock.mockResolvedValue(data({
      sessions: [session('active'), session('peer1')],
      recent_activity: [activity(1, { paths: ['src/e.ts'] })],
      last_external_paths: ['src/e.ts'],
    }))
    render(<WorkspaceStatus />)
    const pill = await screen.findByRole('button')
    expect(pill).toHaveAttribute('data-external', 'true')
    expect(pill.getAttribute('aria-label')).toMatch(/external changes/)
  })

  it('degrades silently when the API fails', async () => {
    statusMock.mockRejectedValue(new Error('boom'))
    const { container } = render(<WorkspaceStatus />)
    await waitFor(() => expect(statusMock).toHaveBeenCalled())
    expect(container.firstChild).toBeNull()
  })

  it('never renders absolute paths', async () => {
    statusMock.mockResolvedValue(data({
      sessions: [session('active'), session('peer1')],
      recent_activity: [activity(1, { paths: ['src/x.ts'] })],
    }))
    render(<WorkspaceStatus />)
    await userEvent.click(await screen.findByRole('button'))
    expect(document.body.textContent).not.toMatch(/\/w\//)
    expect(document.body.textContent).not.toMatch(/^\//m)
  })
})
