import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FloatingWindow from './FloatingWindow'

function renderWindow(overrides: Partial<Parameters<typeof FloatingWindow>[0]> = {}) {
  const props = {
    title: 'file.txt',
    ariaLabel: 'file.txt',
    focused: true,
    onClose: vi.fn(),
    onFocus: vi.fn(),
    zIndex: 5,
    initialX: 100,
    initialY: 100,
    ...overrides,
  }
  render(<FloatingWindow {...props}><div>content here</div></FloatingWindow>)
  return props
}

describe('FloatingWindow', () => {
  it('renders the title and children', () => {
    renderWindow()
    expect(screen.getByText('file.txt')).toBeInTheDocument()
    expect(screen.getByText('content here')).toBeInTheDocument()
  })

  it('close button calls onClose', async () => {
    const props = renderWindow()
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(props.onClose).toHaveBeenCalled()
  })

  it('mousedown on the window calls onFocus', () => {
    const props = renderWindow()
    fireEvent.mouseDown(screen.getByRole('dialog'))
    expect(props.onFocus).toHaveBeenCalled()
  })
})
