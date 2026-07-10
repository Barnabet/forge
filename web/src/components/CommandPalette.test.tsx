import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import CommandPalette from './CommandPalette'

const created: WireEvent = {
  type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
  name: 'n', cwd: '/w', model: 'm', autonomy: 'yolo',
} as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent(created)
  useForge.setState({ models: [
    { id: 'opus-5', display_name: 'Opus 5', context_window: 1 },
    { id: 'gpt-5', display_name: 'GPT-5', context_window: 1 },
  ] })
})

describe('CommandPalette', () => {
  it('filters by prefix', () => {
    render(<CommandPalette query="co" onClose={() => {}} />)
    expect(screen.getByText('/compact')).toBeInTheDocument()
    expect(screen.queryByText('/model')).not.toBeInTheDocument()
  })

  it('/model steps into the model list and calls the endpoint', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)
    const onClose = vi.fn()
    render(<CommandPalette query="" onClose={onClose} />)
    await userEvent.click(screen.getByText('/model'))
    await userEvent.click(screen.getByText('GPT-5'))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/model', expect.objectContaining({ method: 'POST' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('/compact surfaces the 409 as an inline error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 409, json: async () => ({}) })))
    const onClose = vi.fn()
    render(<CommandPalette query="" onClose={onClose} />)
    await userEvent.click(screen.getByText('/compact'))
    expect(await screen.findByText(/Session is running/)).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('/effort steps into the levels and posts the choice', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    const onClose = vi.fn()
    render(<CommandPalette query="" onClose={onClose} />)
    await userEvent.click(screen.getByText('/effort'))
    await userEvent.click(screen.getByText('high'))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/effort', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ effort: 'high' }),
    }))
    expect(onClose).toHaveBeenCalled()
  })

  it('/new opens the new-session dialog', async () => {
    const onClose = vi.fn()
    render(<CommandPalette query="ne" onClose={onClose} />)
    await userEvent.click(screen.getByText('/new'))
    expect(useForge.getState().dialog).toBe('new-session')
    expect(onClose).toHaveBeenCalled()
  })
})
