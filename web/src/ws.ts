import type { WireEvent } from './protocol'

export interface WsOptions {
  url: string
  cursors(): Record<string, number>
  onEvent(e: WireEvent): void
  onStatus(s: 'connecting' | 'open' | 'closed'): void
  minDelayMs?: number
}

export function startWs(opts: WsOptions): () => void {
  const min = opts.minDelayMs ?? 500
  let delay = min
  let stopped = false
  let ws: WebSocket | null = null

  const connect = () => {
    opts.onStatus('connecting')
    ws = new WebSocket(opts.url)
    ws.onopen = () => {
      delay = min
      // Contract #5: the server blocks until it receives the cursor map.
      ws!.send(JSON.stringify({ cursors: opts.cursors() }))
      opts.onStatus('open')
    }
    ws.onmessage = ev => opts.onEvent(JSON.parse(ev.data as string) as WireEvent)
    ws.onclose = () => {
      opts.onStatus('closed')
      if (stopped) return
      setTimeout(connect, delay)
      delay = Math.min(delay * 2, 8000)
    }
  }

  connect()
  return () => {
    stopped = true
    ws?.close()
  }
}
