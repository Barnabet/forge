import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useForge } from './state/store'
import App from './App'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  url: string
  constructor(url: string) { this.url = url; FakeWebSocket.instances.push(this) }
  send() {}
  close() {}
}

beforeEach(() => {
  vi.restoreAllMocks()
  useForge.setState(useForge.getInitialState(), true)
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      url.includes('/models') ? [] : url.includes('/health') ? { ok: true } : [],
  })))
})

describe('App', () => {
  it('boots: hydrates, opens the websocket, renders the frame', async () => {
    render(<App />)
    expect(await screen.findByText('Forge')).toBeInTheDocument()          // brand
    // Seed a session so the chat column (and its composer) mounts.
    useForge.getState().applyEvent({
      type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
      name: 'hello world', cwd: '/w', model: 'm', autonomy: 'yolo',
    } as never)
    expect(await screen.findByPlaceholderText('Reply, steer, or queue another task…')).toBeInTheDocument()
    expect(FakeWebSocket.instances.length).toBeGreaterThan(0)
    expect(FakeWebSocket.instances[0].url).toMatch(/\/ws$/)
  })

  it('re-runs hydrate on every WS open so each (re)connect backfills gaps', async () => {
    const hydrateSpy = vi.spyOn(useForge.getState(), 'hydrate').mockResolvedValue()
    render(<App />)
    // Boot fires hydrate once.
    await Promise.resolve()
    expect(hydrateSpy).toHaveBeenCalledTimes(1)
    // Simulate the socket opening (and later reconnecting).
    FakeWebSocket.instances[0].onopen?.()
    expect(hydrateSpy).toHaveBeenCalledTimes(2)
    FakeWebSocket.instances[0].onopen?.()
    expect(hydrateSpy).toHaveBeenCalledTimes(3)
  })

  it('survives a hydrate rejection at boot (engine down) and retries on WS open', async () => {
    const err = new Error('engine down')
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const hydrateSpy = vi
      .spyOn(useForge.getState(), 'hydrate')
      .mockRejectedValueOnce(err)
      .mockResolvedValue()
    render(<App />)
    // Frame still renders despite the rejected boot hydrate — no crash.
    expect(await screen.findByText('Forge')).toBeInTheDocument()
    expect(errorSpy).toHaveBeenCalled()
    // When the engine comes back the WS opens and hydrate runs again (retry loop).
    FakeWebSocket.instances[0].onopen?.()
    expect(hydrateSpy).toHaveBeenCalledTimes(2)
  })

  it('renders events pushed through the store', async () => {
    render(<App />)
    useForge.getState().applyEvent({
      type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
      name: 'hello world', cwd: '/w', model: 'm', autonomy: 'yolo',
    } as never)
    expect(await screen.findByText('hello world')).toBeInTheDocument()  // sidebar row
  })

  it('shows the empty state when no session exists', async () => {
    render(<App />)
    expect(await screen.findByText(/No session — create one from the sidebar/)).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Reply, steer, or queue another task…')).not.toBeInTheDocument()
  })
})
