import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { StreamItem } from '../state/reducer'
import PlanCard from './PlanCard'

type Plan = Extract<StreamItem, { kind: 'plan' }>
const plan = (over: Partial<Plan> = {}): Plan => ({
  kind: 'plan', seq: 5, callId: 'p1', plan: '# Migrate to sqlite\n\nSteps here.',
  state: 'pending', feedback: '', ...over,
})

describe('PlanCard', () => {
  it('pending: shows the plan body, badge, and both actions', () => {
    render(<PlanCard item={plan()} onResolve={() => {}} />)
    expect(screen.getByText('awaiting approval')).toBeInTheDocument()
    expect(screen.getByText('Migrate to sqlite')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & execute' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Request changes' })).toBeInTheDocument()
  })

  it('approve resolves without feedback', async () => {
    const onResolve = vi.fn()
    render(<PlanCard item={plan()} onResolve={onResolve} />)
    await userEvent.click(screen.getByRole('button', { name: 'Approve & execute' }))
    expect(onResolve).toHaveBeenCalledWith('approve')
  })

  it('request changes reveals a textarea and sends the feedback', async () => {
    const onResolve = vi.fn()
    render(<PlanCard item={plan()} onResolve={onResolve} />)
    await userEvent.click(screen.getByRole('button', { name: 'Request changes' }))
    await userEvent.type(screen.getByPlaceholderText('What should change?'), 'use postgres')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onResolve).toHaveBeenCalledWith('revise', 'use postgres')
  })

  it('empty feedback does not resolve', async () => {
    const onResolve = vi.fn()
    render(<PlanCard item={plan()} onResolve={onResolve} />)
    await userEvent.click(screen.getByRole('button', { name: 'Request changes' }))
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onResolve).not.toHaveBeenCalled()
  })

  it('approved: collapsed by default, click expands, no actions', async () => {
    render(<PlanCard item={plan({ state: 'approved' })} onResolve={() => {}} />)
    expect(screen.getByText('approved')).toBeInTheDocument()
    expect(screen.queryByText('Migrate to sqlite')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve & execute' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('PLAN'))
    expect(screen.getByText('Migrate to sqlite')).toBeInTheDocument()
  })

  it('revising: shows the requested feedback', () => {
    render(<PlanCard item={plan({ state: 'revising', feedback: 'more tests' })}
                     onResolve={() => {}} />)
    expect(screen.getByText('changes requested')).toBeInTheDocument()
    expect(screen.getByText('Requested: more tests')).toBeInTheDocument()
  })
})
