import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { StreamItem } from '../state/reducer'
import ApprovalGate from './ApprovalGate'

type Gate = Extract<StreamItem, { kind: 'gate' }>
const gate: Gate = {
  kind: 'gate', seq: 5, callId: 'c1', tool: 'bash', display: 'rm -rf build', denied: false,
}

describe('ApprovalGate', () => {
  it('renders title, command, and the three buttons', () => {
    render(<ApprovalGate item={gate} onResolve={() => {}} />)
    expect(screen.getByText('Approval required')).toBeInTheDocument()
    expect(screen.getByText('rm -rf build')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Allow' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Always/ })).toBeInTheDocument()
  })

  it('Allow and Deny resolve without a policy', async () => {
    const onResolve = vi.fn()
    render(<ApprovalGate item={gate} onResolve={onResolve} />)
    await userEvent.click(screen.getByRole('button', { name: 'Allow' }))
    expect(onResolve).toHaveBeenCalledWith('allow', undefined)
    await userEvent.click(screen.getByRole('button', { name: 'Deny' }))
    expect(onResolve).toHaveBeenCalledWith('deny', undefined)
  })

  it('Always dropdown resolves allow with the chosen policy', async () => {
    const onResolve = vi.fn()
    render(<ApprovalGate item={gate} onResolve={onResolve} />)
    await userEvent.click(screen.getByRole('button', { name: /Always/ }))
    await userEvent.click(screen.getByText('Always allow bash (session)'))
    expect(onResolve).toHaveBeenCalledWith('allow', { pattern: '*', scope: 'session' })
  })

  it('denied gate renders the collapsed row', () => {
    render(<ApprovalGate item={{ ...gate, denied: true }} onResolve={() => {}} />)
    expect(screen.getByText(/Denied/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow' })).not.toBeInTheDocument()
  })
})
