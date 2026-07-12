import { beforeEach, describe, expect, it } from 'vitest'
import { useForge } from './store'

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
})

describe('file-index status slice', () => {
  it('file_index_progress updates fileIndex without creating a session', () => {
    const { applyEvent } = useForge.getState()
    applyEvent({
      type: 'file_index_progress', project_id: 'p1',
      state: 'indexing', done: 3, total: 10,
    })
    expect(useForge.getState().fileIndex.p1).toEqual({
      state: 'indexing', done: 3, total: 10,
    })
    // no phantom session was created for a project-scoped event
    expect(useForge.getState().order).toHaveLength(0)

    applyEvent({
      type: 'file_index_progress', project_id: 'p1',
      state: 'ready', done: 10, total: 10,
    })
    expect(useForge.getState().fileIndex.p1.state).toBe('ready')
  })
})

describe('viewer store slice', () => {
  it('openViewer adds a viewer, dedupes by sid+path, and focuses the existing one', () => {
    const { openViewer } = useForge.getState()
    openViewer('aa', 'a.txt')
    openViewer('aa', 'b.txt')
    expect(useForge.getState().viewers).toHaveLength(2)
    const bZ = useForge.getState().viewers.find(v => v.path === 'b.txt')!.z

    // Re-opening a.txt does not add a duplicate but raises it above b.txt.
    openViewer('aa', 'a.txt')
    const viewers = useForge.getState().viewers
    expect(viewers).toHaveLength(2)
    const aZ = viewers.find(v => v.path === 'a.txt')!.z
    expect(aZ).toBeGreaterThan(bZ)
  })

  it('closeViewer removes the viewer', () => {
    const { openViewer, closeViewer } = useForge.getState()
    openViewer('aa', 'a.txt')
    const id = useForge.getState().viewers[0].id
    closeViewer(id)
    expect(useForge.getState().viewers).toHaveLength(0)
  })

  it('focusViewer raises a window to the top z', () => {
    const { openViewer, focusViewer } = useForge.getState()
    openViewer('aa', 'a.txt')
    openViewer('aa', 'b.txt')
    const a = useForge.getState().viewers.find(v => v.path === 'a.txt')!
    focusViewer(a.id)
    const viewers = useForge.getState().viewers
    const aZ = viewers.find(v => v.path === 'a.txt')!.z
    const bZ = viewers.find(v => v.path === 'b.txt')!.z
    expect(aZ).toBeGreaterThan(bZ)
  })
})
