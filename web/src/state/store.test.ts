import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cursors, useForge } from './store'
import type { WireEvent } from '../protocol'

const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  vi.restoreAllMocks()
})

describe('store', () => {
  it('applyEvent routes to the right session, creating it on demand', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'one', cwd: '/w', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('user_message', 'aa', 2, { text: 'hi' }))
    const s = useForge.getState()
    expect(s.order).toEqual(['aa'])
    expect(s.sessions['aa'].stream.items).toHaveLength(1)
    expect(s.activeId).toBe('aa') // first session becomes active
  })

  it('cursors reports lastSeq per session', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('user_message', 'aa', 2, { text: 'x' }))
    expect(cursors(useForge.getState())).toEqual({ aa: 2 })
  })

  it('hydrate seeds sessions from REST and loads models/health', async () => {
    const meta = { id: 'aa', name: 'restored', cwd: '/w', model: 'm1', autonomy: 'guarded', status: 'idle' }
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
      ok: true,
      json: async () =>
        url.includes('/models') ? [{ id: 'm1', display_name: 'Model One', context_window: 1 }]
        : url.includes('/health') ? { ok: true }
        : url.includes('/events') ? []
        : [meta],
    })) as unknown as typeof fetch)
    await useForge.getState().hydrate()
    const s = useForge.getState()
    expect(s.order).toEqual(['aa'])
    expect(s.sessions['aa'].stream).toMatchObject({ name: 'restored', model: 'm1', autonomy: 'guarded', status: 'idle' })
    expect(s.models[0].display_name).toBe('Model One')
    expect(s.healthy).toBe(true)
  })

  it('hydrate backfills events over REST after each session lastSeq, deduping overlap', async () => {
    const { applyEvent } = useForge.getState()
    // Seed a session that already has events applied up to seq 2 (lastSeq === 2).
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('user_message', 'aa', 2, { text: 'two' }))
    expect(useForge.getState().sessions['aa'].stream.lastSeq).toBe(2)

    const meta = { id: 'aa', name: 'n', cwd: '/', model: 'm', autonomy: 'yolo', status: 'idle' }
    const backfill = [
      ev('user_message', 'aa', 2, { text: 'DUPLICATE' }), // overlap <= lastSeq, must be deduped
      ev('user_message', 'aa', 3, { text: 'three' }),
      ev('user_message', 'aa', 4, { text: 'four' }),
    ]
    const fetchSpy = vi.fn(async (url: string) => ({
      ok: true,
      json: async () =>
        url.includes('/models') ? []
        : url.includes('/health') ? { ok: true }
        : url.includes('/events') ? backfill
        : [meta],
    }))
    vi.stubGlobal('fetch', fetchSpy as unknown as typeof fetch)

    await useForge.getState().hydrate()

    // Backfill request used the session's lastSeq as the cursor.
    expect(fetchSpy).toHaveBeenCalledWith('/api/sessions/aa/events?after=2')
    const stream = useForge.getState().sessions['aa'].stream
    expect(stream.lastSeq).toBe(4)
    const texts = stream.items.filter(i => i.kind === 'user').map(i => (i as { text: string }).text)
    expect(texts).toEqual(['two', 'three', 'four']) // overlap dropped, backfill appended
  })

  it('openDrawer fetches changesets and sets state; closeDrawer keeps index', async () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => [{ index: 0, path: '/w/a.py', added: 1, removed: 0, diff: '', status: 'pending' }],
    })) as unknown as typeof fetch)
    await useForge.getState().openDrawer(0)
    let s = useForge.getState().sessions['aa']
    expect(s.drawer).toMatchObject({ open: true, changesetIndex: 0, view: 'diff' })
    expect(s.changesets).toHaveLength(1)
    useForge.getState().closeDrawer()
    s = useForge.getState().sessions['aa']
    expect(s.drawer.open).toBe(false)
  })
})

describe('store: v1.1', () => {
  it('session_deleted removes the session and re-activates a survivor', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'a', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('session_created', 'bb', 1, { name: 'b', cwd: '/', model: 'm', autonomy: 'yolo' }))
    useForge.getState().setActive('aa')
    applyEvent({ type: 'session_deleted', session_id: 'aa', seq: 0 } as never)
    const s = useForge.getState()
    expect(s.order).toEqual(['bb'])
    expect(s.activeId).toBe('bb')
    applyEvent({ type: 'session_deleted', session_id: 'bb', seq: 0 } as never)
    expect(useForge.getState().activeId).toBeNull()
  })

  it('deleted-session fallback skips archived survivors', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'a', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('session_created', 'bb', 1, { name: 'b', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('session_created', 'cc', 1, { name: 'c', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('session_archived', 'bb', 2, {}))
    useForge.getState().setActive('aa')
    applyEvent({ type: 'session_deleted', session_id: 'aa', seq: 0 } as never)
    expect(useForge.getState().activeId).toBe('cc')
  })

  it('hydrate loads projects', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
      ok: true,
      json: async () =>
        url.includes('/projects') ? [{ id: 'p1', name: 'mygent', cwd: '/w',
          default_model: '', default_autonomy: '', default_effort: '' }]
        : url.includes('/models') ? []
        : url.includes('/health') ? { ok: true }
        : [],
    })) as unknown as typeof fetch)
    await useForge.getState().hydrate()
    expect(useForge.getState().projects[0]).toMatchObject({ id: 'p1', name: 'mygent' })
  })

  it('newSessionInProject posts the project id and activates the session', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => ({
      ok: true,
      json: async () =>
        init?.method === 'POST'
          ? { id: 'ns', name: 'New session', cwd: '/w', model: 'm',
              autonomy: 'yolo', status: 'idle', project_id: 'p1',
              archived: false, effort: 'high' }
          : [],
    }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    await useForge.getState().newSessionInProject('p1')
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ project_id: 'p1' }),
    }))
    const s = useForge.getState()
    expect(s.activeId).toBe('ns')
    expect(s.sessions['ns'].stream).toMatchObject({ projectId: 'p1', effort: 'high' })
  })

  it('dialog open/close', () => {
    useForge.getState().openDialog('new-project')
    expect(useForge.getState().dialog).toBe('new-project')
    useForge.getState().closeDialog()
    expect(useForge.getState().dialog).toBeNull()
  })
})
