import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ToolItem } from '../lib/toolActivity'
import { useForge } from '../state/store'
import ToolActivity from './ToolActivity'

const base: ToolItem = {
  kind: 'tool', seq: 1, callId: 'c1', tool: 'bash', display: 'pytest -q',
  status: 'running', output: '', durationMs: 0, diffStats: null, autoApproved: false,
  images: [],
}
const t = (over: Partial<ToolItem>): ToolItem => ({ ...base, ...over })

const DIFF = '--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n import os\n-x = 1\n+x = 2\n+y = 3\n'

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent({
    type: 'session_created', session_id: 'aa', seq: 1, ts: 0,
    name: 'n', cwd: '/w', model: 'm', autonomy: 'yolo',
  } as never)
  useForge.getState().setActive('aa')
  vi.restoreAllMocks()
})

describe('ToolActivity single line', () => {
  it('pending: about-to verb stands alone, no object', () => {
    const { container } = render(<ToolActivity
      items={[t({ tool: 'read_file', display: '', status: 'running', pending: true })]}
    />)
    expect(screen.getByText('About to read')).toBeInTheDocument()
    expect(container.querySelector('[class*="object"]')).toBeNull()
  })

  it('running: present-tense verb, no duration', () => {
    render(<ToolActivity items={[base]} />)
    expect(screen.getByText('Running')).toBeInTheDocument()
    expect(screen.getByText('pytest -q')).toBeInTheDocument()
  })

  it('done: past-tense verb, duration, auto-approved; output collapsed', () => {
    render(<ToolActivity
      items={[t({ status: 'done', durationMs: 1240, autoApproved: true, output: '3 passed' })]}
    />)
    expect(screen.getByText('Ran')).toBeInTheDocument()
    expect(screen.getByText('1.2s')).toBeInTheDocument()
    expect(screen.getByText('auto-approved')).toBeInTheDocument()
    expect(screen.queryByText('3 passed')).not.toBeInTheDocument()
  })

  it('error: failed tag; output revealed on click', async () => {
    render(<ToolActivity items={[t({ status: 'error', output: 'boom' })]} />)
    expect(screen.getByText('failed')).toBeInTheDocument()
    expect(screen.queryByText('boom')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('pytest -q'))
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('truncates long output to the last 12 lines when expanded', async () => {
    const output = Array.from({ length: 20 }, (_, i) => `line${i + 1}`).join('\n')
    render(<ToolActivity items={[t({ status: 'done', output })]} />)
    await userEvent.click(screen.getByText('pytest -q'))
    expect(screen.getByText('… 8 earlier lines')).toBeInTheDocument()
    expect(screen.getByText(/line20/)).toBeInTheDocument()
    expect(screen.queryByText(/line1$/m)).not.toBeInTheDocument()
  })

  it('edit line: verb and diff chips visible, diff collapsed by default', async () => {
    render(<ToolActivity
      items={[t({ tool: 'edit_file', display: 'app.py', status: 'done',
                  diffStats: { path: '/w/app.py', added: 41, removed: 38, changeset_index: 2, diff: DIFF } })]}
    />)
    expect(screen.getByText('Edited')).toBeInTheDocument()
    expect(screen.getByText('+41')).toBeInTheDocument()
    expect(screen.getByText('−38')).toBeInTheDocument()
    expect(screen.queryByText('x = 2')).not.toBeInTheDocument()
    expect(screen.queryByText('@@ -1,2 +1,3 @@')).not.toBeInTheDocument()

    await userEvent.click(screen.getByText('app.py'))
    expect(screen.getByText('x = 2')).toBeInTheDocument()
    expect(screen.getByText('@@ -1,2 +1,3 @@')).toBeInTheDocument()
  })

  it('Revert calls the api and marks the line reverted', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    render(<ToolActivity
      items={[t({ tool: 'edit_file', display: 'app.py', status: 'done',
                  diffStats: { path: '/w/app.py', added: 1, removed: 0, changeset_index: 2, diff: DIFF } })]}
    />)
    await userEvent.click(screen.getByRole('button', { name: 'Revert' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/aa/changesets/2/revert', expect.anything())
    expect(screen.getByRole('button', { name: 'reverted' })).toBeDisabled()
  })

  it('Revert does not toggle the diff disclosure', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch)
    render(<ToolActivity
      items={[t({ tool: 'edit_file', display: 'app.py', status: 'done',
                  diffStats: { path: '/w/app.py', added: 1, removed: 0, changeset_index: 0, diff: DIFF } })]}
    />)
    await userEvent.click(screen.getByRole('button', { name: 'Revert' }))
    expect(screen.queryByText('x = 2')).not.toBeInTheDocument()
  })

  it('write line diff is collapsed by default and can be expanded', async () => {
    render(<ToolActivity
      items={[t({ tool: 'write_file', display: 'new.py', status: 'done',
                  diffStats: { path: '/w/new.py', added: 3, removed: 0, changeset_index: 3, diff: DIFF } })]}
    />)
    expect(screen.getByText('Wrote')).toBeInTheDocument()
    expect(screen.queryByText('x = 2')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('new.py'))
    expect(screen.getByText('x = 2')).toBeInTheDocument()
  })

  it('shows paths relative to the session cwd', () => {
    render(<ToolActivity
      items={[t({ tool: 'read_file', display: '/w/proj/src/app.py', status: 'done' })]}
      cwd="/w/proj"
    />)
    expect(screen.getByText('src/app.py')).toBeInTheDocument()
  })

  it('hides the raw (no output) sentinel body', () => {
    render(<ToolActivity items={[t({ status: 'done', output: '(no output)' })]} />)
    expect(screen.queryByText('(no output)')).not.toBeInTheDocument()
  })

  it('done with (no output): shows an explicit, subtle no-output marker', () => {
    render(<ToolActivity items={[t({ status: 'done', output: '(no output)' })]} />)
    expect(screen.getByText('no output')).toBeInTheDocument()
  })

  it('done with empty output: shows the no-output marker', () => {
    render(<ToolActivity items={[t({ status: 'done', output: '' })]} />)
    expect(screen.getByText('no output')).toBeInTheDocument()
  })

  it('running/pending with no output yet: no marker (distinguishable from completed)', () => {
    render(<ToolActivity items={[t({ status: 'running', output: '' })]} />)
    expect(screen.queryByText('no output')).not.toBeInTheDocument()
  })

  it('done with real output: no marker, output stays collapsed', () => {
    render(<ToolActivity items={[t({ status: 'done', output: '3 passed' })]} />)
    expect(screen.queryByText('no output')).not.toBeInTheDocument()
  })

  it('error with no output: no success marker', () => {
    render(<ToolActivity items={[t({ status: 'error', output: '' })]} />)
    expect(screen.queryByText('no output')).not.toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
  })

  it('done with a diff body: no marker', () => {
    render(<ToolActivity
      items={[t({ tool: 'edit_file', display: 'app.py', status: 'done',
                  diffStats: { path: '/w/app.py', added: 1, removed: 0, changeset_index: 2, diff: DIFF } })]}
    />)
    expect(screen.queryByText('no output')).not.toBeInTheDocument()
  })
})

describe('ToolActivity group', () => {
  const reads = [
    t({ tool: 'read_file', callId: 'r1', display: 'a.py', status: 'done', output: 'A' }),
    t({ tool: 'read_file', callId: 'r2', display: 'b.py', status: 'done', output: 'B' }),
  ]

  it('collapses to a family roll-up line', () => {
    render(<ToolActivity items={reads} />)
    expect(screen.getByText('Read 2 files')).toBeInTheDocument()
    expect(screen.queryByText('a.py')).not.toBeInTheDocument()
  })

  it('uses present tense while a member runs', () => {
    render(<ToolActivity
      items={[reads[0], t({ tool: 'read_file', callId: 'r2', display: 'b.py', status: 'running' })]}
    />)
    expect(screen.getByText('Reading 2 files')).toBeInTheDocument()
  })

  it('expands to individual lines, each expandable to output', async () => {
    render(<ToolActivity items={reads} />)
    await userEvent.click(screen.getByText('Read 2 files'))
    expect(screen.getByText('a.py')).toBeInTheDocument()
    expect(screen.getByText('b.py')).toBeInTheDocument()
    expect(screen.queryByText('A')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('a.py'))
    expect(screen.getByText('A')).toBeInTheDocument()
    expect(screen.queryByText('B')).not.toBeInTheDocument()
  })

  it('edit group: names the file, counts edits, sums diff stats', () => {
    render(<ToolActivity
      items={[
        t({ tool: 'edit_file', callId: 'e1', display: 'app.py', status: 'done',
            diffStats: { path: '/w/app.py', added: 10, removed: 2, changeset_index: 0 } }),
        t({ tool: 'edit_file', callId: 'e2', display: 'app.py', status: 'done',
            diffStats: { path: '/w/app.py', added: 5, removed: 1, changeset_index: 1 } }),
      ]}
    />)
    expect(screen.getByText('Edited')).toBeInTheDocument()
    expect(screen.getByText('app.py')).toBeInTheDocument()
    expect(screen.getByText('× 2')).toBeInTheDocument()
    expect(screen.getByText('+15')).toBeInTheDocument()
    expect(screen.getByText('−3')).toBeInTheDocument()
  })

  it('edit group expands to collapsed individual diffs', async () => {
    render(<ToolActivity
      items={[
        t({ tool: 'edit_file', callId: 'e1', display: 'app.py', status: 'done',
            diffStats: { path: '/w/app.py', added: 10, removed: 2, changeset_index: 0, diff: DIFF } }),
        t({ tool: 'edit_file', callId: 'e2', display: 'app.py', status: 'done',
            diffStats: { path: '/w/app.py', added: 5, removed: 1, changeset_index: 1, diff: DIFF } }),
      ]}
    />)
    await userEvent.click(screen.getByText('× 2'))
    expect(screen.queryByText('x = 2')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Revert' })).toHaveLength(2)

    await userEvent.click(screen.getAllByText('app.py')[1])
    expect(screen.getAllByText('x = 2')).toHaveLength(1)
  })

  it('surfaces the failure count on the roll-up', () => {
    render(<ToolActivity
      items={[...reads, t({ tool: 'read_file', callId: 'r3', display: 'c.py', status: 'error' })]}
    />)
    expect(screen.getByText('Read 3 files')).toBeInTheDocument()
    expect(screen.getByText('1 failed')).toBeInTheDocument()
  })
})
