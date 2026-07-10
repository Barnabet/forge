import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import ConfirmDialog from './ConfirmDialog'
import NewProjectDialog from './NewProjectDialog'
import NewSessionDialog from './NewSessionDialog'

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.setState({ models: [{ id: 'opus-5', display_name: 'Opus 5', context_window: 1 }] })
  vi.restoreAllMocks()
})

describe('ConfirmDialog', () => {
  it('renders and wires both buttons', async () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    render(<ConfirmDialog title="Delete session" body="This is permanent."
                          confirmLabel="Delete" onConfirm={onConfirm} onCancel={onCancel} />)
    expect(screen.getByText('This is permanent.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalled()
  })
})

describe('NewSessionDialog', () => {
  it('shows recents, submits the typed path, closes on success', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => ({
      ok: true,
      json: async () =>
        url.includes('recent_dirs') ? ['/w/one', '/w/two']
        : init?.method === 'POST'
          ? { id: 'ns', name: 'New session', cwd: '/w/one', model: 'm',
              autonomy: 'yolo', status: 'idle', archived: false, effort: 'default' }
          : [],
    }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    useForge.setState({ dialog: 'new-session' })
    render(<NewSessionDialog />)
    await userEvent.click(await screen.findByText('/w/one'))  // recent fills the input
    expect(screen.getByPlaceholderText('/path/to/folder')).toHaveValue('/w/one')
    await userEvent.click(screen.getByRole('button', { name: 'Start session' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ cwd: '/w/one' }),
    }))
    expect(useForge.getState().dialog).toBeNull()
  })

  it('surfaces a 400 inline and stays open', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => ({
      ok: !(init?.method === 'POST'),
      status: init?.method === 'POST' ? 400 : 200,
      json: async () => (url.includes('recent_dirs') ? [] : {}),
    })) as unknown as typeof fetch)
    useForge.setState({ dialog: 'new-session' })
    render(<NewSessionDialog />)
    await userEvent.type(screen.getByPlaceholderText('/path/to/folder'), '/nope')
    await userEvent.click(screen.getByRole('button', { name: 'Start session' }))
    expect(await screen.findByText(/not a valid folder/i)).toBeInTheDocument()
    expect(useForge.getState().dialog).toBe('new-session')
  })
})

describe('NewProjectDialog', () => {
  it('creates the project with chosen defaults and closes', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => ({
      ok: true,
      json: async () =>
        url.includes('recent_dirs') ? []
        : init?.method === 'POST'
          ? { id: 'p1', name: 'mygent', cwd: '/w', default_model: '',
              default_autonomy: 'guarded', default_effort: 'high' }
          : [],
    }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    useForge.setState({ dialog: 'new-project' })
    render(<NewProjectDialog />)
    await userEvent.type(screen.getByPlaceholderText('Project name'), 'mygent')
    await userEvent.type(screen.getByPlaceholderText('/path/to/folder'), '/w')
    await userEvent.selectOptions(screen.getByLabelText('Autonomy'), 'guarded')
    await userEvent.selectOptions(screen.getByLabelText('Effort'), 'high')
    await userEvent.click(screen.getByRole('button', { name: 'Create project' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/projects', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ name: 'mygent', cwd: '/w',
        default_autonomy: 'guarded', default_effort: 'high' }),
    }))
    expect(useForge.getState().dialog).toBeNull()
  })
})
