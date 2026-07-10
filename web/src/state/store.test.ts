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
        : [meta],
    })) as unknown as typeof fetch)
    await useForge.getState().hydrate()
    const s = useForge.getState()
    expect(s.order).toEqual(['aa'])
    expect(s.sessions['aa'].stream).toMatchObject({ name: 'restored', model: 'm1', autonomy: 'guarded', status: 'idle' })
    expect(s.models[0].display_name).toBe('Model One')
    expect(s.healthy).toBe(true)
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
