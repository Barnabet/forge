import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import FilePicker from './FilePicker'

const created: WireEvent = {
  type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
  name: 'n', cwd: '/w', model: 'm', autonomy: 'yolo',
} as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent(created)
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

describe('FilePicker', () => {
  it('debounces, fetches, renders results, picks on click', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ['src/app.py', 'src/api.py'] }))
    vi.stubGlobal('fetch', fetchMock)
    render(<FilePicker query="ap" onPick={() => {}} />)
    await vi.advanceTimersByTimeAsync(200)
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/files?q=ap')
    expect(await screen.findByText('src/app.py')).toBeInTheDocument()
  })

  it('onPick receives the clicked path', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ['src/app.py'] })))
    const onPick = vi.fn()
    render(<FilePicker query="ap" onPick={onPick} />)
    await vi.advanceTimersByTimeAsync(200)
    await userEvent.click(await screen.findByText('src/app.py'))
    expect(onPick).toHaveBeenCalledWith('src/app.py')
  })
})
