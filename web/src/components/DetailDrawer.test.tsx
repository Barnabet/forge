import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { Changeset, WireEvent } from '../protocol'
import DetailDrawer from './DetailDrawer'

const created: WireEvent = {
  type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
  name: 'n', cwd: '/w', model: 'm', autonomy: 'yolo',
} as unknown as WireEvent

const cs: Changeset[] = [
  { index: 0, path: '/w/src/app.py', added: 2, removed: 1, status: 'pending',
    diff: '--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n import os\n-x = 1\n+x = 2\n+y = 3\n' },
  { index: 1, path: '/w/README.md', added: 1, removed: 0, status: 'pending',
    diff: '--- a/README.md\n+++ b/README.md\n@@ -1,0 +1,1 @@\n+hello\n' },
] as Changeset[]

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent(created)
  useForge.setState(s => ({
    sessions: {
      ...s.sessions,
      aa: { ...s.sessions.aa, changesets: cs,
            drawer: { open: true, changesetIndex: 0, view: 'diff' } },
    },
  }))
})

describe('DetailDrawer', () => {
  it('renders breadcrumb, stat chips, and the parsed diff', () => {
    render(<DetailDrawer />)
    expect(screen.getByText('src/')).toBeInTheDocument()
    expect(screen.getByText('app.py')).toBeInTheDocument()
    expect(screen.getByText('+2')).toBeInTheDocument()
    expect(screen.getByText('−1')).toBeInTheDocument()
    expect(screen.getByText('x = 2')).toBeInTheDocument()
    expect(screen.getByText('@@ -1,2 +1,3 @@')).toBeInTheDocument()
  })

  it('footer shows the pager and steps files', async () => {
    render(<DetailDrawer />)
    expect(screen.getByText('1 of 2 files changed')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '›' }))
    expect(useForge.getState().sessions.aa.drawer.changesetIndex).toBe(1)
  })

  it('Revert and Keep all hit the API', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => [] }))
    vi.stubGlobal('fetch', fetchMock)
    render(<DetailDrawer />)
    await userEvent.click(screen.getByRole('button', { name: 'Revert' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/changesets/0/revert', expect.anything())
    await userEvent.click(screen.getByRole('button', { name: 'Keep all' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/changesets/keep_all', expect.anything())
  })

  it('File view renders the cached content; Blame is stubbed', async () => {
    useForge.setState(s => ({
      sessions: { ...s.sessions, aa: { ...s.sessions.aa, fileContent: 'import os\nx = 2\n' } },
    }))
    render(<DetailDrawer />)
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ path: '/w/src/app.py', content: 'import os\nx = 2\n' }) }))
    vi.stubGlobal('fetch', fetchMock)
    await userEvent.click(screen.getByRole('button', { name: 'File' }))
    expect(await screen.findByText(/import os/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Blame' }))
    expect(screen.getByText('Blame — post-V1')).toBeInTheDocument()
  })
})
