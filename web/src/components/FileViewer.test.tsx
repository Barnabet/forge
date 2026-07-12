import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import FileViewer from './FileViewer'

afterEach(() => vi.unstubAllGlobals())

describe('FileViewer', () => {
  it('renders text content with line numbers', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, text: async () => 'line one\nline two\nline three',
    })))
    render(<FileViewer sid="aa" path="src/notes.txt" />)
    expect(await screen.findByText(/line one/)).toBeInTheDocument()
    // gutter has line numbers 1..3
    expect(screen.getByText((_, el) => el?.tagName === 'PRE' && el.textContent === '1\n2\n3\n'))
      .toBeInTheDocument()
  })

  it('syntax-highlights known file types', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, text: async () => 'const x = 1',
    })))
    const { container } = render(<FileViewer sid="aa" path="src/main.ts" />)
    await vi.waitFor(() => {
      expect(container.querySelector('pre.shiki')).toBeInTheDocument()
    })
    expect(container.querySelector('pre.shiki span')).toBeInTheDocument()
  })

  it('renders an image via <img>', () => {
    render(<FileViewer sid="aa" path="pics/logo.png" />)
    const img = screen.getByRole('img')
    expect(img).toHaveAttribute('src', '/api/sessions/aa/fs/file?path=pics%2Flogo.png')
  })

  it('shows a fallback with a download link when the fetch fails (413)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 413, text: async () => '' })))
    render(<FileViewer sid="aa" path="data/huge.bin" />)
    expect(await screen.findByText('Cannot preview this file')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'Download' })
    expect(link).toHaveAttribute('href', '/api/sessions/aa/fs/file?path=data%2Fhuge.bin')
    expect(link).toHaveAttribute('download')
  })

  it('shows a fallback when content looks binary', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => 'abc\0def' })))
    render(<FileViewer sid="aa" path="data/thing" />)
    expect(await screen.findByText('Cannot preview this file')).toBeInTheDocument()
  })
})
