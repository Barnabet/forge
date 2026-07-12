import { useEffect, useMemo, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import {
  TERMINAL_DOCK_MAX_WIDTH, TERMINAL_DOCK_MIN_WIDTH, useForge,
} from '../state/store'
import { type TerminalRecord } from '../state/terminals'
import s from './TerminalDock.module.css'

// Stable empty references so derived `order`/`records` keep the same identity
// across renders (avoids churning memo/effect deps for a session with no
// terminals yet).
const EMPTY_ORDER: string[] = []
const EMPTY_RECORDS: Record<string, TerminalRecord> = {}

// Read a terminal's live lifecycle straight from the store (never a closure
// snapshot) so async handlers registered once on mount stay current.
function liveRunning(sid: string, tid: string): boolean {
  const st = useForge.getState().sessions[sid]?.stream.terminals.records[tid]?.state
  return st === 'running' || st === 'starting'
}

// Substring of `str` starting `byteStart` UTF-8 bytes in. Server offsets fall on
// whole-character boundaries, so the byte tail never splits a rune.
const _enc = new TextEncoder()
const _dec = new TextDecoder()
function sliceFromByte(str: string, byteStart: number): string {
  if (byteStart <= 0) return str
  const bytes = _enc.encode(str)
  if (byteStart >= bytes.length) return ''
  return _dec.decode(bytes.subarray(byteStart))
}

function leaf(path: string): string {
  const slash = path.lastIndexOf('/')
  return slash < 0 ? path : path.slice(slash + 1)
}

function basename(cmd: string[]): string {
  return leaf(cmd[0] ?? '') || 'shell'
}

// Shells invoked as `sh -c "<cmd>"` (the common runtime shape) would otherwise
// all label as "sh"; surface the first token of the wrapped command instead so
// tabs read as `npm`, `python`, etc. rather than the interpreter.
const SHELLS = new Set(['sh', 'bash', 'zsh', 'dash', 'ash', 'ksh', 'fish'])
function commandLabel(cmd: string[]): string {
  const base = basename(cmd)
  if (SHELLS.has(base) && cmd[1] === '-c' && cmd[2]) {
    const first = cmd[2].trim().split(/\s+/)[0] ?? ''
    return leaf(first) || base
  }
  return base
}

function shortId(id: string): string {
  return id.length <= 8 ? id : id.slice(0, 8)
}

type Signal = 'live' | 'attention' | 'ended' | 'quiet'
function signalOf(rec: TerminalRecord | undefined): Signal {
  if (!rec) return 'quiet'
  if (rec.state === 'running' || rec.state === 'starting')
    return rec.unread ? 'attention' : 'live'
  if (rec.state === 'orphaned') return 'attention'
  return 'ended' // exited / closed
}

const STATE_LABEL: Record<TerminalRecord['state'], string> = {
  starting: 'starting', running: 'running', exited: 'exited',
  closed: 'closed', orphaned: 'orphaned',
}

// Map existing semantic tokens onto the xterm ANSI palette. Reads live computed
// values so it tracks the active theme.
function readTheme(el: HTMLElement) {
  const cs = getComputedStyle(el)
  const v = (name: string) => cs.getPropertyValue(name).trim()
  return {
    background: v('--bg-app') || '#0a0a0c',
    foreground: v('--text-body') || '#b9b9c2',
    cursor: v('--accent') || '#35e0c2',
    cursorAccent: v('--bg-app') || '#0a0a0c',
    selectionBackground: v('--tint-6') || 'rgba(255,255,255,0.08)',
    black: v('--text-ghost-2'), red: v('--danger'), green: v('--ok'),
    yellow: v('--warn'), blue: v('--signal-live'), magenta: v('--accent'),
    cyan: v('--ok-dim'), white: v('--text-secondary'),
    brightBlack: v('--text-faint'), brightRed: v('--danger-dim'),
    brightGreen: v('--ok-dim'), brightYellow: v('--warn-title'),
    brightBlue: v('--signal-live'), brightMagenta: v('--accent'),
    brightCyan: v('--ok'), brightWhite: v('--text-primary'),
  }
}

export default function TerminalDock() {
  const activeId = useForge(st => st.activeId)
  const session = useForge(st => (st.activeId ? st.sessions[st.activeId] : undefined))
  const selectedMap = useForge(st => st.selectedTerminal)
  const uiTheme = useForge(st => st.uiTheme)
  const dockWidth = useForge(st => st.terminalDockWidth)
  const setDockWidth = useForge(st => st.setTerminalDockWidth)
  const setDockOpen = useForge(st => st.setTerminalDockOpen)

  const terminals = session?.stream.terminals
  const order = terminals?.order ?? EMPTY_ORDER
  const records = terminals?.records ?? EMPTY_RECORDS
  const selected = activeId ? selectedMap[activeId] : undefined
  const selectedId = useMemo(() => {
    if (selected && records[selected]) return selected
    return order[order.length - 1]
  }, [selected, records, order])
  const rec = selectedId ? records[selectedId] : undefined
  const running = rec?.state === 'running' || rec?.state === 'starting'

  const hostRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  // What the xterm view has consumed, keyed to a terminal id.
  const viewRef = useRef<{ tid: string | null; start: number; end: number }>({
    tid: null, start: 0, end: 0,
  })
  const resizeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [resizing, setResizing] = useState(false)

  // Create the single xterm instance on mount.
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const term = new Terminal({
      convertEol: true, cursorBlink: false, scrollback: 5000,
      fontFamily: getComputedStyle(host).getPropertyValue('--font-mono') || 'monospace',
      fontSize: 12.5, theme: readTheme(host),
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host)
    try { fit.fit() } catch { /* zero-size during mount */ }
    termRef.current = term
    fitRef.current = fit
    const dataSub = term.onData(data => {
      const st = useForge.getState()
      const sid = st.activeId
      const cur = viewRef.current.tid
      if (sid && cur && liveRunning(sid, cur))
        void st.writeTerminal(sid, cur, data).catch(() => undefined)
    })
    const ro = new ResizeObserver(() => {
      try { fit.fit() } catch { /* detached */ }
      scheduleResize()
    })
    ro.observe(host)
    return () => {
      if (resizeTimer.current) clearTimeout(resizeTimer.current)
      dataSub.dispose()
      ro.disconnect()
      term.dispose()
      termRef.current = null
      fitRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Re-theme when the app theme flips.
  useEffect(() => {
    const term = termRef.current
    const host = hostRef.current
    if (term && host) term.options.theme = readTheme(host)
  }, [uiTheme])

  // Feed output to xterm: switch, append suffix, or full rewrite on replacement.
  useEffect(() => {
    const term = termRef.current
    if (!term) return
    const view = viewRef.current
    if (!rec || !selectedId) {
      if (view.tid !== null) { term.reset(); viewRef.current = { tid: null, start: 0, end: 0 } }
      return
    }
    const switched = view.tid !== selectedId
    // A clear-local-view or store replacement shifts the window backwards.
    const replaced = rec.startOffset > view.start || rec.endOffset < view.end
    if (switched || replaced) {
      term.reset()
      term.write(rec.output)
      viewRef.current = { tid: selectedId, start: rec.startOffset, end: rec.endOffset }
      try { fitRef.current?.fit() } catch { /* detached */ }
      return
    }
    if (rec.endOffset > view.end) {
      const suffix = sliceFromByte(rec.output, view.end - rec.startOffset)
      term.write(suffix)
      viewRef.current = { tid: selectedId, start: rec.startOffset, end: rec.endOffset }
    }
  }, [rec, selectedId])

  // Hydrate promptly when the selected record needs it (no request storm: the
  // flag clears once the buffer lands).
  const needsHydration = rec?.needsHydration ?? false
  useEffect(() => {
    if (needsHydration && activeId) void useForge.getState().hydrateTerminals(activeId)
  }, [needsHydration, activeId, selectedId])

  const scheduleResize = () => {
    if (resizeTimer.current) clearTimeout(resizeTimer.current)
    resizeTimer.current = setTimeout(() => {
      const term = termRef.current
      const st = useForge.getState()
      const sid = st.activeId
      const cur = viewRef.current.tid
      if (term && sid && cur && liveRunning(sid, cur))
        void st.resizeTerminal(sid, cur, term.cols, term.rows).catch(() => undefined)
    }, 200)
  }

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault()
    setResizing(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev: PointerEvent) => setDockWidth(window.innerWidth - ev.clientX)
    const onUp = () => {
      setResizing(false)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  const act = async (fn: () => Promise<void>) => {
    try { await fn() } catch { /* surfaced via record.error / quiet */ }
  }

  const empty = order.length === 0
  const signal = signalOf(rec)

  return (
    <section
      className={s.dock}
      data-resizing={resizing || undefined}
      aria-label="Terminals"
      style={{ width: dockWidth }}
    >
      <div
        className={s.resizer}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize terminals"
        aria-valuemin={TERMINAL_DOCK_MIN_WIDTH}
        aria-valuemax={TERMINAL_DOCK_MAX_WIDTH}
        aria-valuenow={dockWidth}
        onPointerDown={startResize}
      />
      <div className={s.rail} data-signal={signal} aria-hidden />
      <header className={s.header}>
        <span className={s.eyebrow}>TERMINALS</span>
        <span className={s.selected} title={rec ? `${rec.command.join(' ')} — ${rec.cwd}` : ''}>
          {rec ? (
            <>
              <span className={s.cmd}>{rec.command.join(' ') || basename(rec.command)}</span>
              <span className={s.cwd}>{rec.cwd}</span>
            </>
          ) : null}
        </span>
        {rec && <span className={s.state} data-signal={signal}>{STATE_LABEL[rec.state]}</span>}
        <span className={s.controls}>
          <button
            className={s.iconBtn} title="Interrupt (SIGINT)" aria-label="Interrupt terminal"
            disabled={!running || !activeId || !selectedId}
            onClick={() => activeId && selectedId && void act(() =>
              useForge.getState().signalTerminal(activeId, selectedId, 'INT'))}
          >⌃C</button>
          <button
            className={s.iconBtn} title="Kill (SIGKILL)" aria-label="Kill terminal"
            disabled={!running || !activeId || !selectedId}
            onClick={() => activeId && selectedId && void act(() =>
              useForge.getState().signalTerminal(activeId, selectedId, 'KILL'))}
          >✕K</button>
          <button
            className={s.iconBtn} title="Clear view" aria-label="Clear terminal view"
            disabled={!selectedId}
            onClick={() => activeId && selectedId &&
              useForge.getState().clearTerminalOutput(activeId, selectedId)}
          >⌫</button>
          <button
            className={s.iconBtn} title="Close terminal" aria-label="Close terminal"
            disabled={!running || !activeId || !selectedId}
            onClick={() => activeId && selectedId && void act(() =>
              useForge.getState().closeTerminal(activeId, selectedId))}
          >⏻</button>
          <button
            className={s.iconBtn} title="Hide terminals" aria-label="Hide terminals"
            onClick={() => activeId && setDockOpen(activeId, false)}
          >»</button>
        </span>
      </header>
      {!empty && (
        <div className={s.tabs} role="tablist" aria-label="Open terminals">
          {order.map(id => {
            const r = records[id]
            const sg = signalOf(r)
            return (
              <button
                key={id} role="tab" aria-selected={id === selectedId}
                className={s.tab} data-selected={id === selectedId || undefined}
                aria-label={`Terminal ${shortId(id)} ${commandLabel(r.command)} ${STATE_LABEL[r.state]}`}
                onClick={() => activeId && useForge.getState().selectTerminal(activeId, id)}
              >
                <span className={s.mark} data-signal={sg} aria-hidden />
                <span className={s.tabId}>{shortId(id)}</span>
                <span className={s.tabCmd}>{commandLabel(r.command)}</span>
                {r.unread && id !== selectedId && <span className={s.unread} aria-hidden />}
              </button>
            )
          })}
        </div>
      )}
      <div className={s.body}>
        <div
          ref={hostRef} className={s.canvas} data-empty={empty || undefined}
          role="group" aria-label="Terminal output"
        />
        {empty && (
          <div className={s.emptyCopy}>
            Agents open displayed terminals for servers, interactive commands, and live output.
          </div>
        )}
        {rec?.error && <div className={s.error} role="status">{rec.error}</div>}
      </div>
    </section>
  )
}
