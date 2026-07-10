import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ToolItem } from '../lib/toolActivity'
import ToolActivity from './ToolActivity'

const base: ToolItem = {
  kind: 'tool', seq: 1, callId: 'c1', tool: 'bash', display: 'pytest -q',
  status: 'running', output: '', durationMs: 0, diffStats: null, autoApproved: false,
}
const t = (over: Partial<ToolItem>): ToolItem => ({ ...base, ...over })

describe('ToolActivity single line', () => {
  it('running: present-tense verb, no duration', () => {
    render(<ToolActivity items={[base]} onOpenPanel={() => {}} />)
    expect(screen.getByText('Running')).toBeInTheDocument()
    expect(screen.getByText('pytest -q')).toBeInTheDocument()
  })

  it('done: past-tense verb, duration, auto-approved; output collapsed', () => {
    render(<ToolActivity
      items={[t({ status: 'done', durationMs: 1240, autoApproved: true, output: '3 passed' })]}
      onOpenPanel={() => {}}
    />)
    expect(screen.getByText('Ran')).toBeInTheDocument()
    expect(screen.getByText('1.2s')).toBeInTheDocument()
    expect(screen.getByText('auto-approved')).toBeInTheDocument()
    expect(screen.queryByText('3 passed')).not.toBeInTheDocument()
  })

  it('error: failed tag; output revealed on click', async () => {
    render(<ToolActivity items={[t({ status: 'error', output: 'boom' })]} onOpenPanel={() => {}} />)
    expect(screen.getByText('failed')).toBeInTheDocument()
    expect(screen.queryByText('boom')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('pytest -q'))
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('truncates long output to the last 12 lines when expanded', async () => {
    const output = Array.from({ length: 20 }, (_, i) => `line${i + 1}`).join('\n')
    render(<ToolActivity items={[t({ status: 'done', output })]} onOpenPanel={() => {}} />)
    await userEvent.click(screen.getByText('pytest -q'))
    expect(screen.getByText('… 8 earlier lines')).toBeInTheDocument()
    expect(screen.getByText(/line20/)).toBeInTheDocument()
    expect(screen.queryByText(/line1$/m)).not.toBeInTheDocument()
  })

  it('edit line: verb, diff chips, open panel', async () => {
    const onOpen = vi.fn()
    render(<ToolActivity
      items={[t({ tool: 'edit_file', display: 'app.py', status: 'done',
                  diffStats: { path: '/w/app.py', added: 41, removed: 38, changeset_index: 2 } })]}
      onOpenPanel={onOpen}
    />)
    expect(screen.getByText('Edited')).toBeInTheDocument()
    expect(screen.getByText('+41')).toBeInTheDocument()
    expect(screen.getByText('−38')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /open panel/ }))
    expect(onOpen).toHaveBeenCalledWith(2)
  })

  it('open panel does not toggle the output collapse', async () => {
    const onOpen = vi.fn()
    render(<ToolActivity
      items={[t({ tool: 'edit_file', display: 'app.py', status: 'done', output: 'ok',
                  diffStats: { path: '/w/app.py', added: 1, removed: 0, changeset_index: 0 } })]}
      onOpenPanel={onOpen}
    />)
    await userEvent.click(screen.getByRole('button', { name: /open panel/ }))
    expect(onOpen).toHaveBeenCalled()
    expect(screen.queryByText('ok')).not.toBeInTheDocument()
  })

  it('shows paths relative to the session cwd', () => {
    render(<ToolActivity
      items={[t({ tool: 'read_file', display: '/w/proj/src/app.py', status: 'done' })]}
      cwd="/w/proj"
      onOpenPanel={() => {}}
    />)
    expect(screen.getByText('src/app.py')).toBeInTheDocument()
  })

  it('hides the body for (no output)', () => {
    render(<ToolActivity items={[t({ status: 'done', output: '(no output)' })]} onOpenPanel={() => {}} />)
    expect(screen.queryByText('(no output)')).not.toBeInTheDocument()
  })
})

describe('ToolActivity group', () => {
  const reads = [
    t({ tool: 'read_file', callId: 'r1', display: 'a.py', status: 'done', output: 'A' }),
    t({ tool: 'read_file', callId: 'r2', display: 'b.py', status: 'done', output: 'B' }),
  ]

  it('collapses to a family roll-up line', () => {
    render(<ToolActivity items={reads} onOpenPanel={() => {}} />)
    expect(screen.getByText('Read 2 files')).toBeInTheDocument()
    expect(screen.queryByText('a.py')).not.toBeInTheDocument()
  })

  it('uses present tense while a member runs', () => {
    render(<ToolActivity
      items={[reads[0], t({ tool: 'read_file', callId: 'r2', display: 'b.py', status: 'running' })]}
      onOpenPanel={() => {}}
    />)
    expect(screen.getByText('Reading 2 files')).toBeInTheDocument()
  })

  it('expands to individual lines, each expandable to output', async () => {
    render(<ToolActivity items={reads} onOpenPanel={() => {}} />)
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
      onOpenPanel={() => {}}
    />)
    expect(screen.getByText('Edited')).toBeInTheDocument()
    expect(screen.getByText('app.py')).toBeInTheDocument()
    expect(screen.getByText('× 2')).toBeInTheDocument()
    expect(screen.getByText('+15')).toBeInTheDocument()
    expect(screen.getByText('−3')).toBeInTheDocument()
  })

  it('edit group expands to individual edits with their own panel links', async () => {
    const onOpen = vi.fn()
    render(<ToolActivity
      items={[
        t({ tool: 'edit_file', callId: 'e1', display: 'app.py', status: 'done',
            diffStats: { path: '/w/app.py', added: 10, removed: 2, changeset_index: 0 } }),
        t({ tool: 'edit_file', callId: 'e2', display: 'app.py', status: 'done',
            diffStats: { path: '/w/app.py', added: 5, removed: 1, changeset_index: 1 } }),
      ]}
      onOpenPanel={onOpen}
    />)
    await userEvent.click(screen.getByText('× 2'))
    const links = screen.getAllByRole('button', { name: /open panel/ })
    expect(links).toHaveLength(2)
    await userEvent.click(links[1])
    expect(onOpen).toHaveBeenCalledWith(1)
  })

  it('surfaces the failure count on the roll-up', () => {
    render(<ToolActivity
      items={[...reads, t({ tool: 'read_file', callId: 'r3', display: 'c.py', status: 'error' })]}
      onOpenPanel={() => {}}
    />)
    expect(screen.getByText('Read 3 files')).toBeInTheDocument()
    expect(screen.getByText('1 failed')).toBeInTheDocument()
  })
})
