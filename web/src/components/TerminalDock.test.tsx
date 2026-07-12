import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'

// --- xterm / fit-addon mocks -------------------------------------------------
// A single shared spy surface so tests can assert what the dock wrote/reset and
// can drive the onData (keyboard) callback the component registers on mount.

interface FakeTerm {
  options: { theme?: unknown }
  cols: number
  rows: number
  write: ReturnType<typeof vi.fn>
  reset: ReturnType<typeof vi.fn>
  dispose: ReturnType<typeof vi.fn>
  open: ReturnType<typeof vi.fn>
  loadAddon: ReturnType<typeof vi.fn>
  onData: ReturnType<typeof vi.fn>
  _emitData?: (d: string) => void
  _dataDisposed?: boolean
}

let terms: FakeTerm[] = []
let fitFit = vi.fn()
const fitDisposed = { count: 0 }

vi.mock('@xterm/xterm', () => ({
  Terminal: vi.fn(function (this: FakeTerm) {
    const self: FakeTerm = {
      options: {},
      cols: 80,
      rows: 24,
      write: vi.fn(),
      reset: vi.fn(),
      dispose: vi.fn(),
      open: vi.fn(),
      loadAddon: vi.fn(),
      onData: vi.fn((cb: (d: string) => void) => {
        self._emitData = cb
        return { dispose: vi.fn(() => { self._dataDisposed = true }) }
      }),
    }
    terms.push(self)
    return self
  }),
}))

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: vi.fn(function () {
    return { fit: (...a: unknown[]) => fitFit(...a), dispose: () => { fitDisposed.count++ } }
  }),
}))

vi.mock('@xterm/xterm/css/xterm.css', () => ({}))

// jsdom lacks ResizeObserver; capture the callback so tests can trigger it.
let roCb: (() => void) | null = null
class FakeResizeObserver {
  constructor(cb: () => void) { roCb = cb }
  observe() { /* noop */ }
  disconnect() { roCb = null }
}
vi.stubGlobal('ResizeObserver', FakeResizeObserver)

import TerminalDock from './TerminalDock'

const term = () => terms[terms.length - 1]

let nextSeq = 1
const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

function seedSession(sid = 'aa') {
  useForge.getState().applyEvent(
    ev('session_created', sid, nextSeq++, { name: 's', cwd: '/w', model: 'm', autonomy: 'yolo' }))
  useForge.getState().setActive(sid)
}

function addTerminal(sid: string, tid: string, over: object = {}) {
  useForge.getState().applyEvent(ev('terminal_state', sid, nextSeq++, {
    terminal_id: tid, command: ['/bin/sh', '-c', 'npm run dev'], cwd: '/w',
    cols: 80, rows: 24, state: 'running', ...over,
  }))
}

function output(sid: string, tid: string, start: number, end: number, text: string) {
  act(() => useForge.getState().applyEvent(ev('terminal_output', sid, 0, {
    terminal_id: tid, start_offset: start, end_offset: end, text,
  })))
}

beforeEach(() => {
  vi.restoreAllMocks()
  useForge.setState(useForge.getInitialState(), true)
  terms = []
  nextSeq = 1
  fitFit = vi.fn()
  fitDisposed.count = 0
  roCb = null
  vi.spyOn(useForge.getState(), 'writeTerminal').mockResolvedValue()
  vi.spyOn(useForge.getState(), 'resizeTerminal').mockResolvedValue()
  vi.spyOn(useForge.getState(), 'signalTerminal').mockResolvedValue()
  vi.spyOn(useForge.getState(), 'closeTerminal').mockResolvedValue()
  vi.spyOn(useForge.getState(), 'hydrateTerminals').mockResolvedValue()
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('TerminalDock', () => {
  it('renders the empty state with no terminals', () => {
    seedSession()
    render(<TerminalDock />)
    expect(screen.getByText(/Agents open displayed terminals/)).toBeInTheDocument()
    expect(screen.queryAllByRole('tab')).toHaveLength(0)
  })

  it('writes initial output to xterm on mount', () => {
    seedSession()
    addTerminal('aa', 't1')
    output('aa', 't1', 0, 5, 'hello')
    render(<TerminalDock />)
    // suffix write of the current buffer
    expect(term().write).toHaveBeenCalledWith('hello')
  })

  it('appends only the new suffix without re-writing seen output', () => {
    seedSession()
    addTerminal('aa', 't1')
    output('aa', 't1', 0, 5, 'hello')
    render(<TerminalDock />)
    term().write.mockClear()
    output('aa', 't1', 5, 11, ' world')
    expect(term().write).toHaveBeenCalledTimes(1)
    expect(term().write).toHaveBeenCalledWith(' world')
  })

  it('labels shell -c commands with the wrapped command, not the interpreter', () => {
    seedSession()
    addTerminal('aa', 't1')
    render(<TerminalDock />)
    expect(screen.getByRole('tab', { name: /npm/ })).toBeInTheDocument()
  })

  it('resets and rewrites on a selected switch', async () => {
    seedSession()
    addTerminal('aa', 't1', { terminal_id: 't1' })
    output('aa', 't1', 0, 3, 'aaa')
    addTerminal('aa', 't2', { terminal_id: 't2', command: ['/bin/sh', '-c', 'python x'] })
    output('aa', 't2', 0, 3, 'bbb')
    render(<TerminalDock />)
    term().reset.mockClear()
    term().write.mockClear()
    await userEvent.click(screen.getByRole('tab', { name: /t1/ }))
    expect(term().reset).toHaveBeenCalled()
    expect(term().write).toHaveBeenCalledWith('aaa')
  })

  it('rewrites when the store window is replaced (dropped buffer)', () => {
    seedSession()
    addTerminal('aa', 't1')
    output('aa', 't1', 0, 3, 'abc')
    render(<TerminalDock />)
    term().reset.mockClear()
    term().write.mockClear()
    // Simulate an authoritative buffer replacing the window (startOffset jumps
    // forward past the retained view — the dock must reset and rewrite).
    act(() => {
      useForge.setState(s => {
        const sess = s.sessions.aa
        const col = sess.stream.terminals
        return { sessions: { ...s.sessions, aa: { ...sess, stream: { ...sess.stream, terminals: {
          ...col, records: { ...col.records, t1: { ...col.records.t1,
            output: 'XYZ', startOffset: 10, endOffset: 13, needsHydration: false } } } } } } }
      })
    })
    expect(term().reset).toHaveBeenCalled()
    expect(term().write).toHaveBeenCalledWith('XYZ')
  })

  it('clears the view then appends future output without old output reappearing', () => {
    seedSession()
    addTerminal('aa', 't1')
    output('aa', 't1', 0, 5, 'hello')
    render(<TerminalDock />)
    term().reset.mockClear()
    term().write.mockClear()
    // Clear collapses the window to endOffset and empties output.
    act(() => useForge.getState().clearTerminalOutput('aa', 't1'))
    expect(term().reset).toHaveBeenCalled()
    // The rewrite of the (now empty) buffer must not resurface 'hello'.
    expect(term().write).not.toHaveBeenCalledWith('hello')
    term().write.mockClear()
    // Future output appends cleanly from the collapsed offset.
    output('aa', 't1', 5, 8, 'abc')
    expect(term().write).toHaveBeenCalledWith('abc')
    expect(term().write).not.toHaveBeenCalledWith('hello')
  })

  it('handles multibyte (UTF-8) suffix offsets correctly', () => {
    seedSession()
    addTerminal('aa', 't1')
    output('aa', 't1', 0, 2, 'é') // 'é' is 2 bytes
    render(<TerminalDock />)
    term().write.mockClear()
    output('aa', 't1', 2, 3, 'x')
    expect(term().write).toHaveBeenCalledWith('x')
  })

  it('sends keyboard input to the live terminal', () => {
    seedSession()
    addTerminal('aa', 't1')
    render(<TerminalDock />)
    const spy = useForge.getState().writeTerminal as ReturnType<typeof vi.fn>
    act(() => term()._emitData?.('l'))
    expect(spy).toHaveBeenCalledWith('aa', 't1', 'l')
  })

  it('does not send keyboard input when the terminal is not running', () => {
    seedSession()
    addTerminal('aa', 't1', { state: 'exited', exit_code: 0 })
    render(<TerminalDock />)
    const spy = useForge.getState().writeTerminal as ReturnType<typeof vi.fn>
    act(() => term()._emitData?.('l'))
    expect(spy).not.toHaveBeenCalled()
  })

  it('interrupts, kills, and closes via the header controls', async () => {
    seedSession()
    addTerminal('aa', 't1')
    render(<TerminalDock />)
    const sig = useForge.getState().signalTerminal as ReturnType<typeof vi.fn>
    const close = useForge.getState().closeTerminal as ReturnType<typeof vi.fn>
    await userEvent.click(screen.getByRole('button', { name: 'Interrupt terminal' }))
    expect(sig).toHaveBeenCalledWith('aa', 't1', 'INT')
    await userEvent.click(screen.getByRole('button', { name: 'Kill terminal' }))
    expect(sig).toHaveBeenCalledWith('aa', 't1', 'KILL')
    await userEvent.click(screen.getByRole('button', { name: 'Close terminal' }))
    expect(close).toHaveBeenCalledWith('aa', 't1')
  })

  it('disables run controls and shows status for an exited terminal', () => {
    seedSession()
    addTerminal('aa', 't1', { state: 'exited', exit_code: 0 })
    render(<TerminalDock />)
    expect(screen.getByRole('button', { name: 'Interrupt terminal' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Kill terminal' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Close terminal' })).toBeDisabled()
    expect(screen.getByText('exited')).toBeInTheDocument()
  })

  it('shows the orphaned status and keeps clear enabled', () => {
    seedSession()
    addTerminal('aa', 't1', { state: 'orphaned' })
    render(<TerminalDock />)
    expect(screen.getByText('orphaned')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear terminal view' })).not.toBeDisabled()
  })

  it('requests hydration once when the selected record needs it', () => {
    seedSession()
    // A terminal learned about mid-stream with pre-existing output needs hydration.
    useForge.getState().applyEvent(ev('terminal_state', 'aa', 2, {
      terminal_id: 't1', command: ['sh'], cwd: '/w', cols: 80, rows: 24,
      state: 'running', output_offset: 20,
    }))
    const spy = useForge.getState().hydrateTerminals as ReturnType<typeof vi.fn>
    render(<TerminalDock />)
    expect(spy).toHaveBeenCalledWith('aa')
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('hides the dock via the hide control', async () => {
    seedSession()
    useForge.getState().setTerminalDockOpen('aa', true)
    render(<TerminalDock />)
    await userEvent.click(screen.getByRole('button', { name: 'Hide terminals' }))
    expect(useForge.getState().terminalDockOpen.aa).toBeUndefined()
  })

  it('debounces resize and reports cols/rows to the store', () => {
    vi.useFakeTimers()
    seedSession()
    addTerminal('aa', 't1')
    render(<TerminalDock />)
    const spy = useForge.getState().resizeTerminal as ReturnType<typeof vi.fn>
    act(() => { roCb?.() })
    expect(spy).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(200) })
    expect(spy).toHaveBeenCalledWith('aa', 't1', 80, 24)
  })

  it('disposes xterm and the data subscription on unmount', () => {
    seedSession()
    addTerminal('aa', 't1')
    const { unmount } = render(<TerminalDock />)
    const t = term()
    unmount()
    expect(t.dispose).toHaveBeenCalled()
    expect(t._dataDisposed).toBe(true)
  })
})
