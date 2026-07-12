import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import Sidebar from './Sidebar'

const ev = (type: string, sid: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: sid, ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  vi.restoreAllMocks()
  // FileExplorer lists the session root on mount; give it an empty listing.
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ entries: [] }) })) as unknown as typeof fetch)
  useForge.setState({
    projects: [{ id: 'p1', name: 'mygent', cwd: '/w', default_model: '',
                 default_autonomy: '', default_effort: '' }],
  })
  const { applyEvent } = useForge.getState()
  applyEvent(ev('session_created', 'aa', 1, { name: 'fix bug', cwd: '/w', model: 'm', autonomy: 'yolo', project_id: 'p1' }))
  applyEvent(ev('session_created', 'bb', 1, { name: 'scratch', cwd: '/tmp', model: 'm', autonomy: 'yolo' }))
})

describe('Sidebar', () => {
  it('prompts to select a session when nothing is active', () => {
    useForge.setState({ activeId: null }) // upsertSession auto-activates the first session
    render(<Sidebar collapsed={false} />)
    expect(screen.getByText('Select a session to browse files')).toBeInTheDocument()
  })

  it('mounts the explorer once a session is active', () => {
    useForge.getState().setActive('aa')
    render(<Sidebar collapsed={false} />)
    expect(screen.getByText('EXPLORER')).toBeInTheDocument()
  })
})
