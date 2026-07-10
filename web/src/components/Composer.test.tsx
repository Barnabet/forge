import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { WireEvent } from '../protocol'
import Composer, { atQuery, paletteQuery } from './Composer'

const ev = (type: string, seq: number, fields: object = {}): WireEvent =>
  ({ type, session_id: 'aa', ts: 0, seq, ...fields }) as unknown as WireEvent

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.getState().applyEvent(
    ev('session_created', 1, { name: 'n', cwd: '/w', model: 'opus-5', autonomy: 'yolo' }))
  useForge.setState({
    models: [{ id: 'opus-5', display_name: 'opus-5', context_window: 1 }],
    healthy: true,
  })
})

describe('Composer', () => {
  it('Enter sends the draft and clears it', async () => {
    const send = vi.fn(async () => {})
    useForge.setState({ send })
    render(<Composer />)
    const box = screen.getByPlaceholderText('Reply, steer, or queue another task…')
    await userEvent.type(box, 'run the tests{Enter}')
    expect(send).toHaveBeenCalledWith('run the tests')
    expect(box).toHaveValue('')
  })

  it('Shift+Enter inserts a newline instead of sending', async () => {
    const send = vi.fn(async () => {})
    useForge.setState({ send })
    render(<Composer />)
    const box = screen.getByPlaceholderText('Reply, steer, or queue another task…')
    await userEvent.type(box, 'line1{Shift>}{Enter}{/Shift}line2')
    expect(send).not.toHaveBeenCalled()
    expect(box).toHaveValue('line1\nline2')
  })

  it('shows the model pill with autonomy and health', () => {
    render(<Composer />)
    expect(screen.getByText('opus-5 · yolo')).toBeInTheDocument()
    useForge.setState({ healthy: false })
    render(<Composer />)
    expect(screen.getAllByTitle('CLIProxyAPI unreachable').length).toBeGreaterThan(0)
  })

  it('pill includes non-default effort', () => {
    useForge.getState().applyEvent(ev('effort_changed', 2, { effort: 'high' }))
    render(<Composer />)
    expect(screen.getByText('opus-5 · high · yolo')).toBeInTheDocument()
  })

  it('archived session locks the composer', () => {
    useForge.getState().applyEvent(ev('session_archived', 2, {}))
    render(<Composer />)
    const box = screen.getByPlaceholderText('Archived — unarchive to continue')
    expect(box).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })
})

describe('draft triggers', () => {
  it('paletteQuery matches only slash-prefixed drafts', () => {
    expect(paletteQuery('/mod')).toBe('mod')
    expect(paletteQuery('/')).toBe('')
    expect(paletteQuery('hello /model')).toBeNull()
  })

  it('atQuery matches a trailing @token', () => {
    expect(atQuery('see @src/ap')).toBe('src/ap')
    expect(atQuery('@')).toBe('')
    expect(atQuery('email me a@b.com ')).toBeNull()  // not at the draft tail
    expect(atQuery('no token')).toBeNull()
  })
})

describe('context usage pill', () => {
  it('shows tokens and percent of the model window', () => {
    useForge.setState({
      models: [{ id: 'opus-5', display_name: 'opus-5', context_window: 200_000 }],
    })
    useForge.getState().applyEvent(
      ev('assistant_message', 2, { text: 'x', tool_calls: [], usage_tokens: 62_000 }))
    render(<Composer />)
    expect(screen.getByText('62k · 31%')).toBeInTheDocument()
    expect(screen.getByTitle(/62,000 of 200,000/)).toBeInTheDocument()
  })

  it('warns at the 75% compaction threshold and hides with no usage', () => {
    useForge.setState({
      models: [{ id: 'opus-5', display_name: 'opus-5', context_window: 100_000 }],
    })
    const { unmount } = render(<Composer />)
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument()  // no usage yet
    unmount()
    useForge.getState().applyEvent(
      ev('assistant_message', 2, { text: 'x', tool_calls: [], usage_tokens: 80_000 }))
    render(<Composer />)
    const pill = screen.getByText('80k · 80%')
    expect(pill).toHaveAttribute('data-warn', 'true')
  })
})
