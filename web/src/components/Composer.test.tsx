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
