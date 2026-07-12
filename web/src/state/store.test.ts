import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cursors, useForge } from './store'
import { api } from '../api'
import type { SessionMeta, WireEvent } from '../protocol'

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

  it('hydrate keeps core sessions/projects when optional index endpoint is unavailable', async () => {
    const meta = { id: 'aa', name: 'restored', cwd: '/w', model: 'm1', autonomy: 'guarded', status: 'idle' }
    const project = { id: 'p1', name: 'Forge', cwd: '/w' }
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/index') return { ok: false, status: 404, json: async () => ({}) }
      return {
        ok: true,
        status: 200,
        json: async () =>
          url.includes('/models') ? []
          : url.includes('/health') ? { ok: true }
          : url.includes('/projects') ? [project]
          : url.includes('/events') ? []
          : [meta],
      }
    }) as unknown as typeof fetch)

    await useForge.getState().hydrate()

    const state = useForge.getState()
    expect(state.sessions['aa'].stream.name).toBe('restored')
    expect(state.projects).toEqual([project])
    expect(state.fileIndex).toEqual({})
    expect(state.healthy).toBe(true)
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

  it('optimistic send anchors thinkingSince on idle→running', async () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({}) })) as unknown as typeof fetch)
    const before = Date.now()
    await useForge.getState().send('go')
    const stream = useForge.getState().sessions['aa'].stream
    expect(stream.status).toBe('running')
    expect(stream.thinkingSince).toBeGreaterThanOrEqual(before)
  })

  it('optimistic send clears thinkingSince when the request fails', async () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('network') }) as unknown as typeof fetch)
    await expect(useForge.getState().send('go')).rejects.toThrow('network')
    const stream = useForge.getState().sessions['aa'].stream
    expect(stream.status).toBe('idle')
    expect(stream.thinkingSince).toBeNull()
  })

  it('submitEdit rewinds-and-replaces at the given seq', async () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'a', cwd: '/', model: 'm', autonomy: 'yolo' }))
    useForge.getState().setActive('aa')
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    await useForge.getState().submitEdit(5, 'new text', [])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/aa/rewind',
      expect.objectContaining({
        body: JSON.stringify({ target_user_seq: 5, text: 'new text', images: [] }),
      }),
    )
  })

  it('revert calls the API with the changeset index', async () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    await useForge.getState().revert(2)
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/changesets/2/revert', expect.anything())
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

  it('hydrate prunes sessions the server no longer has', async () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'a', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('session_created', 'bb', 1, { name: 'b', cwd: '/', model: 'm', autonomy: 'yolo' }))
    useForge.getState().setActive('aa')  // active session is the one being deleted

    // Server only knows about bb now (aa was deleted while offline).
    const meta = { id: 'bb', name: 'b', cwd: '/', model: 'm', autonomy: 'yolo', status: 'idle' }
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
      ok: true,
      json: async () =>
        url.includes('/models') ? []
        : url.includes('/health') ? { ok: true }
        : url.includes('/projects') ? []
        : url.includes('/events') ? []
        : [meta],
    })) as unknown as typeof fetch)

    await useForge.getState().hydrate()

    const s = useForge.getState()
    expect(s.order).toEqual(['bb'])
    expect(s.sessions['aa']).toBeUndefined()
    expect(s.activeId).toBe('bb')  // active fell back to the survivor
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

describe('store: session pill acknowledgment', () => {
  it('selecting an unread session clears it optimistically and acks once', () => {
    const { applyEvent } = useForge.getState()
    // 'bb' becomes active first; the run on the inactive 'aa' stays unread.
    applyEvent(ev('session_created', 'bb', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('run_finished', 'aa', 2, { reason: 'completed', unread: true }))
    expect(useForge.getState().sessions['aa'].stream.unread).toBe(true)
    const spy = vi.spyOn(api, 'markRead').mockResolvedValue(undefined)

    useForge.getState().setActive('aa')

    expect(useForge.getState().sessions['aa'].stream.unread).toBe(false)
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith('aa')
  })

  it('selecting an already-read session does not ack', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    const spy = vi.spyOn(api, 'markRead').mockResolvedValue(undefined)

    useForge.getState().setActive('aa')

    expect(spy).not.toHaveBeenCalled()
  })

  it('a completion landing on the active session auto-acks on arrival', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    useForge.setState({ activeId: 'aa' })
    const spy = vi.spyOn(api, 'markRead').mockResolvedValue(undefined)

    applyEvent(ev('run_finished', 'aa', 2, { reason: 'completed', unread: true }))

    expect(useForge.getState().sessions['aa'].stream.unread).toBe(false)
    expect(spy).toHaveBeenCalledWith('aa')
  })

  it('a completion on an inactive session stays unread and does not ack', () => {
    const { applyEvent } = useForge.getState()
    applyEvent(ev('session_created', 'aa', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    applyEvent(ev('session_created', 'bb', 1, { name: 'n', cwd: '/', model: 'm', autonomy: 'yolo' }))
    useForge.setState({ activeId: 'aa' })
    const spy = vi.spyOn(api, 'markRead').mockResolvedValue(undefined)

    applyEvent(ev('run_finished', 'bb', 2, { reason: 'completed', unread: true }))

    expect(useForge.getState().sessions['bb'].stream.unread).toBe(true)
    expect(spy).not.toHaveBeenCalled()
  })

  it('hydrate acks the remembered active session it restored as unread', async () => {
    localStorage.setItem('forge.active', 'aa')
    const meta = {
      id: 'aa', name: 'n', cwd: '/', model: 'm', autonomy: 'yolo', status: 'idle',
      last_run_reason: 'completed', last_run_seq: 2, unread: true,
    }
    const spy = vi.spyOn(api, 'markRead').mockResolvedValue(undefined)
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
      ok: true,
      json: async () =>
        url.includes('/models') ? []
        : url.includes('/health') ? { ok: true }
        : url.includes('/projects') ? []
        : url.includes('/events') ? []
        : [meta],
    })) as unknown as typeof fetch)

    await useForge.getState().hydrate()

    expect(useForge.getState().activeId).toBe('aa')
    expect(useForge.getState().sessions['aa'].stream.unread).toBe(false)
    expect(spy).toHaveBeenCalledWith('aa')
  })

  it('metadata seeds lastRunSeq so a live ack before backfill clears unread', () => {
    // Seed only from meta (no run_finished replayed yet): lastRunSeq must come
    // from meta.last_run_seq so a live run_acknowledged can match the right run.
    useForge.getState().upsertSession('aa', {
      id: 'aa', name: 'n', cwd: '/', model: 'm', autonomy: 'yolo', status: 'idle',
      last_run_reason: 'completed', last_run_seq: 7, unread: true,
    } as SessionMeta)
    expect(useForge.getState().sessions['aa'].stream.lastRunSeq).toBe(7)

    // A run_acknowledged for that run arrives over the WS before REST backfill.
    useForge.getState().applyEvent(ev('run_acknowledged', 'aa', 8, { run_seq: 7 }))

    expect(useForge.getState().sessions['aa'].stream.unread).toBe(false)
  })
})
