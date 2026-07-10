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
    expect(screen.getByPlaceholderText('Reply, steer, or queue another task…')).toBeInTheDocument()
    expect(FakeWebSocket.instances.length).toBeGreaterThan(0)
    expect(FakeWebSocket.instances[0].url).toMatch(/\/ws$/)
  })

  it('renders events pushed through the store', async () => {
    render(<App />)
    useForge.getState().applyEvent({
      type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
      name: 'hello world', cwd: '/w', model: 'm', autonomy: 'yolo',
    } as never)
    expect(await screen.findByRole('tab', { name: /hello world/ })).toBeInTheDocument()
  })
})
