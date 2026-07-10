import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { startWs } from './ws'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  url: string
  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }
  send(data: string) { this.sent.push(data) }
  close() { this.onclose?.() }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
  vi.useFakeTimers()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('startWs', () => {
  it('sends cursors as the first frame on open', () => {
    const stop = startWs({
      url: 'ws://x/ws', cursors: () => ({ aa: 7 }),
      onEvent: () => {}, onStatus: () => {},
    })
    const ws = FakeWebSocket.instances[0]
    ws.onopen!()
    expect(JSON.parse(ws.sent[0])).toEqual({ cursors: { aa: 7 } })
    stop()
  })

  it('parses frames into events', () => {
    const events: unknown[] = []
    const stop = startWs({
      url: 'ws://x/ws', cursors: () => ({}),
      onEvent: e => events.push(e), onStatus: () => {},
    })
    const ws = FakeWebSocket.instances[0]
    ws.onopen!()
    ws.onmessage!({ data: '{"type":"user_message","session_id":"aa","seq":1,"ts":0,"text":"hi"}' })
    expect(events[0]).toMatchObject({ type: 'user_message', text: 'hi' })
    stop()
  })

  it('reconnects after close with fresh cursors, and stop() ends it', () => {
    let seq = 3
    const statuses: string[] = []
    const stop = startWs({
      url: 'ws://x/ws', cursors: () => ({ aa: seq }),
      onEvent: () => {}, onStatus: s => statuses.push(s), minDelayMs: 1,
    })
    const first = FakeWebSocket.instances[0]
    first.onopen!()
    seq = 9
    first.onclose!()                    // dropped connection
    vi.advanceTimersByTime(50)          // past backoff
    expect(FakeWebSocket.instances).toHaveLength(2)
    const second = FakeWebSocket.instances[1]
    second.onopen!()
    expect(JSON.parse(second.sent[0])).toEqual({ cursors: { aa: 9 } })
    expect(statuses).toEqual(['connecting', 'open', 'closed', 'connecting', 'open'])
    stop()
    second.onclose!()
    vi.advanceTimersByTime(60_000)
    expect(FakeWebSocket.instances).toHaveLength(2)  // no zombie reconnect
  })

  it('stop() during the backoff window cancels the pending reconnect', () => {
    const stop = startWs({
      url: 'ws://x/ws', cursors: () => ({}),
      onEvent: () => {}, onStatus: () => {}, minDelayMs: 1,
    })
    const ws = FakeWebSocket.instances[0]
    ws.onopen!()
    ws.onclose!()                       // dropped: reconnect timer armed
    stop()                              // stop while backoff is pending
    vi.advanceTimersByTime(60_000)
    expect(FakeWebSocket.instances).toHaveLength(1)  // no zombie reconnect
  })
})
