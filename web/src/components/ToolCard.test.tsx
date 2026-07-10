import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { StreamItem } from '../state/reducer'
import ToolCard from './ToolCard'

type Tool = Extract<StreamItem, { kind: 'tool' }>
const base: Tool = {
  kind: 'tool', seq: 1, callId: 'c1', tool: 'bash', display: 'pytest -q',
  status: 'running', output: '', durationMs: 0, diffStats: null, autoApproved: false,
}

describe('ToolCard', () => {
  it('running: shows ▸ and the display line, no meta yet', () => {
    render(<ToolCard item={base} onOpenPanel={() => {}} />)
    expect(screen.getByText('▸')).toBeInTheDocument()
    expect(screen.getByText('pytest -q')).toBeInTheDocument()
  })

  it('done: shows ✓, duration, and auto-approved meta', () => {
    render(<ToolCard item={{ ...base, status: 'done', durationMs: 1240, autoApproved: true, output: '3 passed' }} onOpenPanel={() => {}} />)
    expect(screen.getByText('✓')).toBeInTheDocument()
    expect(screen.getByText('1.2s')).toBeInTheDocument()
    expect(screen.getByText('auto-approved')).toBeInTheDocument()
    expect(screen.getByText('3 passed')).toBeInTheDocument()
  })

  it('error: shows ! and the output', () => {
    render(<ToolCard item={{ ...base, status: 'error', output: 'boom' }} onOpenPanel={() => {}} />)
    expect(screen.getByText('!')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('truncates long output to the last 12 lines', () => {
    const output = Array.from({ length: 20 }, (_, i) => `line${i + 1}`).join('\n')
    render(<ToolCard item={{ ...base, status: 'done', output }} onOpenPanel={() => {}} />)
    expect(screen.getByText('… 8 earlier lines')).toBeInTheDocument()
    expect(screen.getByText(/line20/)).toBeInTheDocument()
    expect(screen.queryByText(/line1$/m)).not.toBeInTheDocument()
  })

  it('diff card: stats chips and open panel →', async () => {
    const onOpen = vi.fn()
    render(<ToolCard
      item={{ ...base, tool: 'edit_file', display: 'app.py', status: 'done',
              diffStats: { path: '/w/app.py', added: 41, removed: 38, changeset_index: 2 } }}
      onOpenPanel={onOpen}
    />)
    expect(screen.getByText('+41')).toBeInTheDocument()
    expect(screen.getByText('−38')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /open panel/ }))
    expect(onOpen).toHaveBeenCalledWith(2)
  })

  it('clicking the header collapses and re-expands the body', async () => {
    render(<ToolCard item={{ ...base, status: 'done', output: '3 passed' }} onOpenPanel={() => {}} />)
    expect(screen.getByText('3 passed')).toBeInTheDocument()
    await userEvent.click(screen.getByText('pytest -q'))
    expect(screen.queryByText('3 passed')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('pytest -q'))
    expect(screen.getByText('3 passed')).toBeInTheDocument()
  })

  it('open panel does not toggle the collapse', async () => {
    const onOpen = vi.fn()
    render(<ToolCard
      item={{ ...base, tool: 'edit_file', display: 'app.py', status: 'done', output: 'ok',
              diffStats: { path: '/w/app.py', added: 1, removed: 0, changeset_index: 0 } }}
      onOpenPanel={onOpen}
    />)
    await userEvent.click(screen.getByRole('button', { name: /open panel/ }))
    expect(onOpen).toHaveBeenCalled()
    expect(screen.getByText('ok')).toBeInTheDocument()  // body still visible
  })

  it('hides the body for (no output)', () => {
    render(<ToolCard item={{ ...base, status: 'done', output: '(no output)' }} onOpenPanel={() => {}} />)
    expect(screen.queryByText('(no output)')).not.toBeInTheDocument()
  })
})
