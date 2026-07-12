import { create } from 'zustand'
import { api, type IndexStatus } from '../api'
import { asIconTheme, type IconThemeId } from '../lib/icons'
import { applyUiTheme, asUiTheme, type UiTheme } from '../lib/theme'
import type { Effort, Mode, ModelInfo, Project, SessionMeta, WireEvent } from '../protocol'
import { emptyStream, reduce, type SessionStream } from './reducer'
import {
  applyTerminalBuffer, patchTerminal, upsertTerminalState, type SessionTerminals,
} from './terminals'

export interface SessionState {
  id: string
  stream: SessionStream
}

function newSessionState(id: string): SessionState {
  return { id, stream: emptyStream() }
}

export const SIDEBAR_MIN_WIDTH = 180
export const SIDEBAR_MAX_WIDTH = 480
function clampSidebarWidth(w: number): number {
  return Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, Math.round(w)))
}

export const TERMINAL_DOCK_MIN_WIDTH = 320
export const TERMINAL_DOCK_MAX_WIDTH = 1200
export const TERMINAL_DOCK_DEFAULT_WIDTH = 560
function clampTerminalDockWidth(w: number): number {
  return Math.max(TERMINAL_DOCK_MIN_WIDTH, Math.min(TERMINAL_DOCK_MAX_WIDTH, Math.round(w)))
}

// Per-session terminal-dock open state, persisted as a JSON map keyed by
// session id so the dock restores its open/closed state per session on refresh.
function loadTerminalDockOpen(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem('forge.terminalDock')
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      const out: Record<string, boolean> = {}
      for (const [k, v] of Object.entries(parsed)) if (v === true) out[k] = true
      return out
    }
  } catch { /* legacy 'open'/'closed' string or corrupt value: start empty */ }
  return {}
}

function seedFromMeta(state: SessionState, meta: SessionMeta): SessionState {
  return {
    ...state,
    stream: {
      ...state.stream,
      name: meta.name ?? state.stream.name,
      cwd: meta.cwd, model: meta.model,
      autonomy: meta.autonomy ?? 'yolo',
      status: meta.status ?? 'idle',
      projectId: meta.project_id ?? state.stream.projectId,
      archived: meta.archived ?? state.stream.archived,
      effort: meta.effort ?? state.stream.effort,
      mode: meta.mode ?? state.stream.mode,
      lastTs: meta.last_message_at ?? state.stream.lastTs,
      lastRunReason: meta.last_run_reason ?? state.stream.lastRunReason,
      unread: meta.unread ?? state.stream.unread,
      lastRunSeq: meta.last_run_seq ?? state.stream.lastRunSeq,
    },
  }
}

export interface Viewer {
  id: string
  sid: string
  path: string
  z: number
}

export interface ForgeState {
  sessions: Record<string, SessionState>
  order: string[]
  activeId: string | null
  viewers: Viewer[]
  models: ModelInfo[]
  projects: Project[]
  fileIndex: Record<string, IndexStatus>
  dialog: 'new-session' | 'new-project' | null
  sidebarCollapsed: boolean
  sidebarWidth: number
  // Terminal dock open state per session id.
  terminalDockOpen: Record<string, boolean>
  terminalDockWidth: number
  // Selected terminal id per session (fallback resolved in selectors/actions).
  selectedTerminal: Record<string, string>
  iconTheme: IconThemeId
  uiTheme: UiTheme
  healthy: boolean
  connection: 'connecting' | 'open' | 'closed'
  upsertSession(id: string, seed?: SessionMeta): void
  applyEvent(e: WireEvent): void
  removeSession(id: string): void
  setActive(id: string): void
  setConnection(c: ForgeState['connection']): void
  hydrate(): Promise<void>
  send(text: string, images?: string[]): Promise<void>
  submitEdit(seq: number, text: string, images?: string[]): Promise<void>
  rewind(seq: number): Promise<void>
  revert(changesetIndex: number): Promise<void>
  refreshHealth(): Promise<void>
  openDialog(d: 'new-session' | 'new-project'): void
  closeDialog(): void
  toggleSidebar(): void
  setSidebarWidth(w: number): void
  hydrateTerminals(sid: string): Promise<void>
  selectTerminal(sid: string, tid: string): void
  setTerminalDockOpen(sid: string, open: boolean): void
  setTerminalDockWidth(w: number): void
  writeTerminal(sid: string, tid: string, data: string): Promise<void>
  resizeTerminal(sid: string, tid: string, cols: number, rows: number): Promise<void>
  signalTerminal(sid: string, tid: string, signal: string): Promise<void>
  closeTerminal(sid: string, tid: string): Promise<void>
  clearTerminalOutput(sid: string, tid: string): void
  setIconTheme(t: IconThemeId): void
  setUiTheme(t: UiTheme): void
  createProject(body: Parameters<typeof api.createProject>[0]): Promise<void>
  archiveSession(sid: string): Promise<void>
  unarchiveSession(sid: string): Promise<void>
  deleteSession(sid: string): Promise<void>
  setEffort(effort: Effort): Promise<void>
  setMode(mode: Mode): Promise<void>
  newSessionInProject(pid: string): Promise<void>
  newAdhocSession(body: { cwd: string; model?: string; autonomy?: string; effort?: string }): Promise<void>
  openViewer(sid: string, path: string): void
  closeViewer(id: string): void
  focusViewer(id: string): void
}

export const useForge = create<ForgeState>()((set, get) => {
  const patchSession = (id: string, patch: Partial<SessionState>) =>
    set(s => ({ sessions: { ...s.sessions, [id]: { ...s.sessions[id], ...patch } } }))

  const patchTerminals = (id: string, fn: (col: SessionTerminals) => SessionTerminals) =>
    set(s => {
      const session = s.sessions[id]
      if (!session) return {}
      const terminals = fn(session.stream.terminals)
      if (terminals === session.stream.terminals) return {}
      return {
        sessions: {
          ...s.sessions,
          [id]: { ...session, stream: { ...session.stream, terminals } },
        },
      }
    })

  const active = () => {
    const { activeId, sessions } = get()
    return activeId ? sessions[activeId] : undefined
  }

  // Clear a session's unread pill optimistically and tell the server, but only
  // when it's actually unread — so repeated opens don't spam POST /read. The
  // ack is best-effort: a failed request must never break navigation, and the
  // server stays authoritative on the next hydrate.
  const acknowledgeRead = (id: string) => {
    const session = get().sessions[id]
    if (!session || !session.stream.unread) return
    patchSession(id, { stream: { ...session.stream, unread: false } })
    api.markRead(id).catch(() => {})
  }

  return {
    sessions: {}, order: [], activeId: null, viewers: [],
    models: [], projects: [], fileIndex: {}, dialog: null,
    sidebarCollapsed: localStorage.getItem('forge.sidebar') === 'collapsed',
    sidebarWidth: clampSidebarWidth(Number(localStorage.getItem('forge.sidebarWidth')) || 232),
    terminalDockOpen: loadTerminalDockOpen(),
    terminalDockWidth: clampTerminalDockWidth(
      Number(localStorage.getItem('forge.terminalDockWidth')) || TERMINAL_DOCK_DEFAULT_WIDTH),
    selectedTerminal: {},
    iconTheme: asIconTheme(localStorage.getItem('forge.iconTheme')),
    uiTheme: asUiTheme(localStorage.getItem('forge.uiTheme')),
    healthy: false, connection: 'connecting',

    upsertSession: (id, seed) =>
      set(s => {
        const existing = s.sessions[id]
        let session = existing ?? newSessionState(id)
        if (seed) session = seedFromMeta(session, seed)
        return {
          sessions: { ...s.sessions, [id]: session },
          order: existing ? s.order : [...s.order, id],
          activeId: s.activeId ?? id,
        }
      }),

    applyEvent: e => {
      // Project-scoped events carry no session_id: route to the file-index
      // status map instead of the per-session reducer.
      if (!('session_id' in e)) {
        set(s => ({
          fileIndex: {
            ...s.fileIndex,
            [e.project_id]: { state: e.state, done: e.done ?? 0, total: e.total ?? 0 },
          },
        }))
        return
      }
      if (e.type === 'session_deleted') {
        get().removeSession(e.session_id)
        return
      }
      get().upsertSession(e.session_id)
      // Detect a terminal newly learned about so we can auto-open/select it.
      const before = get().sessions[e.session_id]?.stream.terminals
      set(s => {
        const session = s.sessions[e.session_id]
        return {
          sessions: {
            ...s.sessions,
            [e.session_id]: { ...session, stream: reduce(session.stream, e) },
          },
        }
      })
      if (e.type === 'terminal_state') {
        const after = get().sessions[e.session_id]?.stream.terminals
        const isNew = before && after && !before.records[e.terminal_id] && !!after.records[e.terminal_id]
        const running = e.state === 'running' || e.state === 'starting'
        if (isNew && running) {
          set(s => ({ selectedTerminal: { ...s.selectedTerminal, [e.session_id]: e.terminal_id } }))
          if (!get().terminalDockOpen[e.session_id]) get().setTerminalDockOpen(e.session_id, true)
        }
      }
      // A completion that lands unread for the session the user is already
      // looking at is read on arrival: ack it immediately (acknowledgeRead is a
      // no-op unless it's actually unread, so this doesn't fire spuriously).
      if (e.type === 'run_finished' && e.session_id === get().activeId)
        acknowledgeRead(e.session_id)
    },

    removeSession: id =>
      set(s => {
        const sessions = { ...s.sessions }
        delete sessions[id]
        const order = s.order.filter(x => x !== id)
        // Drop the stale per-session terminal selection.
        const selectedTerminal = { ...s.selectedTerminal }
        delete selectedTerminal[id]
        // Drop the stale per-session dock-open flag.
        const terminalDockOpen = { ...s.terminalDockOpen }
        if (terminalDockOpen[id]) {
          delete terminalDockOpen[id]
          localStorage.setItem('forge.terminalDock', JSON.stringify(terminalDockOpen))
        }
        let activeId = s.activeId
        if (activeId === id) {
          activeId = order.find(x => !sessions[x].stream.archived) ?? order[0] ?? null
        }
        return { sessions, order, activeId, selectedTerminal, terminalDockOpen }
      }),

    setActive: id => {
      set({ activeId: id })
      acknowledgeRead(id)
    },
    setConnection: connection => set({ connection }),

    hydrate: async () => {
      // Snapshot the ids we already knew BEFORE awaiting, so we only prune true
      // ghosts (present pre-hydrate, absent on the server) and never a session
      // that arrived over the WS while this hydrate was in flight.
      const knownBefore = get().order.slice()
      // Snapshot before upserting: the first upsert makes an arbitrary session
      // active, and the persistence subscription would overwrite this key.
      const remembered = localStorage.getItem('forge.active')
      // File indexing is auxiliary and may be absent when the SPA hot-reloads
      // against an older still-running server. Never let that optional endpoint
      // reject core hydration and hide otherwise healthy sessions/projects.
      const [metas, models, health, projects, fileIndex] = await Promise.all([
        api.sessions(), api.models(), api.health(), api.projects(),
        api.indexStatus().catch(() => ({})),
      ])
      set({ models, healthy: health.ok, projects, fileIndex })
      // Prune sessions the server no longer has (deleted while we were offline).
      // Order matters: prune BEFORE seeding the fresh metas below.
      const serverIds = new Set(metas.map(m => m.id))
      for (const id of knownBefore) {
        if (!serverIds.has(id)) get().removeSession(id)
      }
      for (const m of metas) get().upsertSession(m.id, m)
      // Restore the last-viewed session (upsertSession defaults to whichever
      // session arrived first, which is arbitrary across refreshes).
      if (remembered && serverIds.has(remembered) && get().activeId !== remembered)
        set({ activeId: remembered })
      // Restoring the remembered active session counts as viewing it: clear any
      // unread pill it was seeded with (best-effort, no-op if already read).
      const restored = get().activeId
      if (restored) acknowledgeRead(restored)
      // Backfill each session's stream over REST from its current cursor. This
      // makes boot ordering vs. the WS replay irrelevant (the reducer dedupes by
      // seq) and closes the reconnect/outage gap when re-run on every WS 'open'.
      await Promise.all(metas.map(async m => {
        const after = get().sessions[m.id].stream.lastSeq
        const events = await api.events(m.id, after)
        for (const e of events) get().applyEvent(e)
      }))
    },

    send: async (text, images = []) => {
      const a = active()
      if (!a || (!text.trim() && images.length === 0)) return
      // Optimistic: show the working state immediately; the server's
      // status_changed confirms it (or corrects it to queued). Anchor the
      // Thinking timer now so it starts ticking before the first event lands.
      if (a.stream.status === 'idle')
        patchSession(a.id, { stream: { ...a.stream, status: 'running', thinkingSince: Date.now() } })
      try {
        await api.sendMessage(a.id, text, images)
      } catch (err) {
        const cur = get().sessions[a.id]
        if (cur) patchSession(a.id, { stream: { ...cur.stream, status: 'idle', thinkingSince: null } })
        throw err
      }
    },

    submitEdit: async (seq, text, images = []) => {
      const a = active()
      if (!a) return
      // Atomic edit-and-resend: the server truncates and posts the replacement
      // in one request. Do NOT mutate local history first — the marker event
      // drives the truncation.
      await api.rewind(a.id, seq, { text, images })
    },

    rewind: async seq => {
      const a = active()
      if (!a) return
      await api.rewind(a.id, seq)
    },

    revert: async changesetIndex => {
      const a = active()
      if (!a) return
      await api.revert(a.id, changesetIndex)
    },

    refreshHealth: async () => {
      try {
        set({ healthy: (await api.health()).ok })
      } catch {
        set({ healthy: false })
      }
    },

    openDialog: dialog => set({ dialog }),
    closeDialog: () => set({ dialog: null }),

    toggleSidebar: () =>
      set(s => {
        const sidebarCollapsed = !s.sidebarCollapsed
        localStorage.setItem('forge.sidebar', sidebarCollapsed ? 'collapsed' : 'open')
        return { sidebarCollapsed }
      }),

    setSidebarWidth: w => {
      const sidebarWidth = clampSidebarWidth(w)
      localStorage.setItem('forge.sidebarWidth', String(sidebarWidth))
      set({ sidebarWidth })
    },

    hydrateTerminals: async sid => {
      // Reconcile a session's terminal list, then backfill each one from the
      // offset we already hold. Runs on initial hydrate and every WS reconnect,
      // so it must dedupe against output the WS may have already delivered.
      if (!get().sessions[sid]) return
      let metas
      try {
        metas = await api.listTerminals(sid)
      } catch {
        return // session gone / engine down: leave records as-is
      }
      // The session may have been switched away or deleted while we awaited.
      if (!get().sessions[sid]) return
      for (const m of metas) patchTerminals(sid, col => upsertTerminalState(col, m))
      await Promise.all(metas.map(async m => {
        const cur = get().sessions[sid]
        if (!cur) return
        const rec = cur.stream.terminals.records[m.terminal_id]
        const after = rec?.endOffset ?? 0
        patchTerminals(sid, col => patchTerminal(col, m.terminal_id, { loading: true }))
        try {
          const buf = await api.readTerminal(sid, m.terminal_id, after)
          if (!get().sessions[sid]) return
          patchTerminals(sid, col => applyTerminalBuffer(col, m.terminal_id, buf))
        } catch {
          patchTerminals(sid, col =>
            patchTerminal(col, m.terminal_id, { loading: false, error: 'read failed' }))
        }
      }))
    },

    selectTerminal: (sid, tid) =>
      set(s => {
        const next = { ...s.selectedTerminal, [sid]: tid }
        const session = s.sessions[sid]
        const sessions = session
          ? { ...s.sessions, [sid]: {
              ...session,
              stream: {
                ...session.stream,
                terminals: patchTerminal(session.stream.terminals, tid, { unread: false }),
              },
            } }
          : s.sessions
        return { selectedTerminal: next, sessions }
      }),

    setTerminalDockOpen: (sid, open) => {
      const terminalDockOpen = { ...get().terminalDockOpen }
      if (open) terminalDockOpen[sid] = true
      else delete terminalDockOpen[sid]
      localStorage.setItem('forge.terminalDock', JSON.stringify(terminalDockOpen))
      set({ terminalDockOpen })
    },

    setTerminalDockWidth: w => {
      const terminalDockWidth = clampTerminalDockWidth(w)
      localStorage.setItem('forge.terminalDockWidth', String(terminalDockWidth))
      set({ terminalDockWidth })
    },

    writeTerminal: async (sid, tid, data) => { await api.writeTerminal(sid, tid, data) },
    resizeTerminal: async (sid, tid, cols, rows) => {
      await api.resizeTerminal(sid, tid, cols, rows)
    },
    signalTerminal: async (sid, tid, signal) => { await api.signalTerminal(sid, tid, signal) },
    closeTerminal: async (sid, tid) => { await api.closeTerminal(sid, tid) },

    clearTerminalOutput: (sid, tid) =>
      // View-only clear: drop the retained text and collapse the window to the
      // current end. Keeping startOffset === endOffset preserves the
      // byte-length invariant and still lets the next chunk merge/dedupe against
      // endOffset (no rehydration needed).
      patchTerminals(sid, col => {
        const rec = col.records[tid]
        if (!rec) return col
        return patchTerminal(col, tid, {
          output: '', startOffset: rec.endOffset, unread: false,
        })
      }),

    setIconTheme: iconTheme => {
      localStorage.setItem('forge.iconTheme', iconTheme)
      set({ iconTheme })
    },

    setUiTheme: uiTheme => {
      localStorage.setItem('forge.uiTheme', uiTheme)
      applyUiTheme(uiTheme)
      set({ uiTheme })
    },

    createProject: async body => {
      await api.createProject(body)
      set({ projects: await api.projects() })
    },

    archiveSession: async sid => { await api.archiveSession(sid) },
    unarchiveSession: async sid => { await api.unarchiveSession(sid) },
    deleteSession: async sid => { await api.deleteSession(sid) },

    setEffort: async effort => {
      const a = active()
      if (a) await api.setEffort(a.id, effort)
    },

    setMode: async mode => {
      const a = active()
      if (a) await api.setMode(a.id, mode)
    },

    newSessionInProject: async pid => {
      const meta = await api.createSession({ project_id: pid })
      get().upsertSession(meta.id, meta)
      set({ activeId: meta.id })
    },

    newAdhocSession: async body => {
      const meta = await api.createSession(body)
      get().upsertSession(meta.id, meta)
      set({ activeId: meta.id })
    },

    openViewer: (sid, path) =>
      set(s => {
        const topZ = s.viewers.reduce((m, v) => Math.max(m, v.z), 0)
        const existing = s.viewers.find(v => v.sid === sid && v.path === path)
        if (existing) {
          return {
            viewers: s.viewers.map(v =>
              v.id === existing.id ? { ...v, z: topZ + 1 } : v),
          }
        }
        const id = `${sid}:${path}:${Date.now()}`
        return { viewers: [...s.viewers, { id, sid, path, z: topZ + 1 }] }
      }),

    closeViewer: id =>
      set(s => ({ viewers: s.viewers.filter(v => v.id !== id) })),

    focusViewer: id =>
      set(s => {
        const topZ = s.viewers.reduce((m, v) => Math.max(m, v.z), 0)
        const v = s.viewers.find(x => x.id === id)
        if (!v || v.z === topZ) return {}
        return { viewers: s.viewers.map(x => x.id === id ? { ...x, z: topZ + 1 } : x) }
      }),
  }
})

// Remember the active session across refreshes/restarts (restored in hydrate).
useForge.subscribe((s, prev) => {
  if (s.activeId && s.activeId !== prev.activeId)
    localStorage.setItem('forge.active', s.activeId)
})

export function cursors(state: ForgeState): Record<string, number> {
  return Object.fromEntries(state.order.map(id => [id, state.sessions[id].stream.lastSeq]))
}
