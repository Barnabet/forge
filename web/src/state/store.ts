import { create } from 'zustand'
import { api } from '../api'
import type { Changeset, ModelInfo, SessionMeta, WireEvent } from '../protocol'
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
    },
  }
}

export interface ForgeState {
  sessions: Record<string, SessionState>
  order: string[]
  activeId: string | null
  models: ModelInfo[]
  healthy: boolean
  connection: 'connecting' | 'open' | 'closed'
  upsertSession(id: string, seed?: SessionMeta): void
  applyEvent(e: WireEvent): void
  setActive(id: string): void
  setConnection(c: ForgeState['connection']): void
  hydrate(): Promise<void>
  newSession(): Promise<void>
  send(text: string): Promise<void>
  openDrawer(changesetIndex: number): Promise<void>
  setDrawerView(view: DrawerState['view']): Promise<void>
  closeDrawer(): void
  stepDrawer(delta: 1 | -1): Promise<void>
  revert(): Promise<void>
  keepAll(): Promise<void>
  refreshHealth(): Promise<void>
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
    models: [], healthy: false, connection: 'connecting',

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

    setActive: id => set({ activeId: id }),
    setConnection: connection => set({ connection }),

    hydrate: async () => {
      const [metas, models, health] = await Promise.all([
        api.sessions(), api.models(), api.health(),
      ])
      set({ models, healthy: health.ok })
      for (const m of metas) get().upsertSession(m.id, m)
      // Backfill each session's stream over REST from its current cursor. This
      // makes boot ordering vs. the WS replay irrelevant (the reducer dedupes by
      // seq) and closes the reconnect/outage gap when re-run on every WS 'open'.
      await Promise.all(metas.map(async m => {
        const after = get().sessions[m.id].stream.lastSeq
        const events = await api.events(m.id, after)
        for (const e of events) get().applyEvent(e)
      }))
    },

    newSession: async () => {
      const meta = await api.createSession()
      get().upsertSession(meta.id, meta)
      set({ activeId: meta.id })
    },

    send: async text => {
      const a = active()
      if (a && text.trim()) await api.sendMessage(a.id, text)
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
  }
})

export function cursors(state: ForgeState): Record<string, number> {
  return Object.fromEntries(state.order.map(id => [id, state.sessions[id].stream.lastSeq]))
}
