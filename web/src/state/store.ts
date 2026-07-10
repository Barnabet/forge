import { create } from 'zustand'
import { api } from '../api'
import type { Changeset, Effort, Mode, ModelInfo, Project, SessionMeta, WireEvent } from '../protocol'
import { emptyStream, reduce, type SessionStream } from './reducer'

export interface DrawerState {
  open: boolean
  changesetIndex: number
  view: 'diff' | 'file' | 'blame'
}

export interface SessionState {
  id: string
  stream: SessionStream
  drawer: DrawerState
  changesets: Changeset[]
  fileContent: string | null
}

function newSessionState(id: string): SessionState {
  return {
    id, stream: emptyStream(),
    drawer: { open: false, changesetIndex: 0, view: 'diff' },
    changesets: [], fileContent: null,
  }
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
    },
  }
}

export interface ForgeState {
  sessions: Record<string, SessionState>
  order: string[]
  activeId: string | null
  models: ModelInfo[]
  projects: Project[]
  dialog: 'new-session' | 'new-project' | null
  sidebarCollapsed: boolean
  healthy: boolean
  connection: 'connecting' | 'open' | 'closed'
  upsertSession(id: string, seed?: SessionMeta): void
  applyEvent(e: WireEvent): void
  removeSession(id: string): void
  setActive(id: string): void
  setConnection(c: ForgeState['connection']): void
  hydrate(): Promise<void>
  send(text: string, images?: string[]): Promise<void>
  openDrawer(changesetIndex: number): Promise<void>
  setDrawerView(view: DrawerState['view']): Promise<void>
  closeDrawer(): void
  stepDrawer(delta: 1 | -1): Promise<void>
  revert(): Promise<void>
  keepAll(): Promise<void>
  refreshHealth(): Promise<void>
  openDialog(d: 'new-session' | 'new-project'): void
  closeDialog(): void
  toggleSidebar(): void
  createProject(body: Parameters<typeof api.createProject>[0]): Promise<void>
  archiveSession(sid: string): Promise<void>
  unarchiveSession(sid: string): Promise<void>
  deleteSession(sid: string): Promise<void>
  setEffort(effort: Effort): Promise<void>
  setMode(mode: Mode): Promise<void>
  newSessionInProject(pid: string): Promise<void>
  newAdhocSession(body: { cwd: string; model?: string; autonomy?: string; effort?: string }): Promise<void>
}

export const useForge = create<ForgeState>()((set, get) => {
  const patchSession = (id: string, patch: Partial<SessionState>) =>
    set(s => ({ sessions: { ...s.sessions, [id]: { ...s.sessions[id], ...patch } } }))

  const active = () => {
    const { activeId, sessions } = get()
    return activeId ? sessions[activeId] : undefined
  }

  const loadDrawerFile = async (id: string, index: number, view: string) => {
    if (view !== 'file') return
    const { content } = await api.changesetFile(id, index)
    patchSession(id, { fileContent: content })
  }

  return {
    sessions: {}, order: [], activeId: null,
    models: [], projects: [], dialog: null,
    sidebarCollapsed: localStorage.getItem('forge.sidebar') === 'collapsed',
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
      if (e.type === 'session_deleted') {
        get().removeSession(e.session_id)
        return
      }
      get().upsertSession(e.session_id)
      set(s => {
        const session = s.sessions[e.session_id]
        return {
          sessions: {
            ...s.sessions,
            [e.session_id]: { ...session, stream: reduce(session.stream, e) },
          },
        }
      })
    },

    removeSession: id =>
      set(s => {
        const sessions = { ...s.sessions }
        delete sessions[id]
        const order = s.order.filter(x => x !== id)
        let activeId = s.activeId
        if (activeId === id) {
          activeId = order.find(x => !sessions[x].stream.archived) ?? order[0] ?? null
        }
        return { sessions, order, activeId }
      }),

    setActive: id => set({ activeId: id }),
    setConnection: connection => set({ connection }),

    hydrate: async () => {
      // Snapshot the ids we already knew BEFORE awaiting, so we only prune true
      // ghosts (present pre-hydrate, absent on the server) and never a session
      // that arrived over the WS while this hydrate was in flight.
      const knownBefore = get().order.slice()
      // Snapshot before upserting: the first upsert makes an arbitrary session
      // active, and the persistence subscription would overwrite this key.
      const remembered = localStorage.getItem('forge.active')
      const [metas, models, health, projects] = await Promise.all([
        api.sessions(), api.models(), api.health(), api.projects(),
      ])
      set({ models, healthy: health.ok, projects })
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
      // status_changed confirms it (or corrects it to queued).
      if (a.stream.status === 'idle')
        patchSession(a.id, { stream: { ...a.stream, status: 'running' } })
      try {
        await api.sendMessage(a.id, text, images)
      } catch (err) {
        const cur = get().sessions[a.id]
        if (cur) patchSession(a.id, { stream: { ...cur.stream, status: 'idle' } })
        throw err
      }
    },

    openDrawer: async changesetIndex => {
      const a = active()
      if (!a) return
      const changesets = await api.changesets(a.id)
      patchSession(a.id, {
        changesets, fileContent: null,
        drawer: { open: true, changesetIndex, view: 'diff' },
      })
    },

    setDrawerView: async view => {
      const a = active()
      if (!a) return
      patchSession(a.id, { drawer: { ...a.drawer, view } })
      await loadDrawerFile(a.id, a.drawer.changesetIndex, view)
    },

    closeDrawer: () => {
      const a = active()
      if (a) patchSession(a.id, { drawer: { ...a.drawer, open: false } })
    },

    stepDrawer: async delta => {
      const a = active()
      if (!a || a.changesets.length === 0) return
      const n = a.changesets.length
      const changesetIndex = (a.drawer.changesetIndex + delta + n) % n
      patchSession(a.id, { drawer: { ...a.drawer, changesetIndex }, fileContent: null })
      await loadDrawerFile(a.id, changesetIndex, a.drawer.view)
    },

    revert: async () => {
      const a = active()
      if (!a) return
      await api.revert(a.id, a.drawer.changesetIndex)
      patchSession(a.id, { changesets: await api.changesets(a.id) })
    },

    keepAll: async () => {
      const a = active()
      if (!a) return
      await api.keepAll(a.id)
      patchSession(a.id, { changesets: await api.changesets(a.id) })
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
