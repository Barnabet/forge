import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type { FsEntry } from '../api'
import FileExplorer from './FileExplorer'

vi.mock('../api', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, message: string) { super(message); this.status = status }
  },
  api: {
    fsList: vi.fn(),
    fsMkdir: vi.fn(async () => undefined),
    fsTouch: vi.fn(async () => undefined),
    fsMove: vi.fn(async () => undefined),
    fsDelete: vi.fn(async () => undefined),
    fsUpload: vi.fn(async () => undefined),
  },
}))

import { api } from '../api'

const entry = (name: string, type: 'file' | 'dir'): FsEntry => ({ name, type, size: 0, mtime: 0 })

const listMock = api.fsList as unknown as ReturnType<typeof vi.fn>

const setList = (map: Record<string, FsEntry[]>) => {
  listMock.mockImplementation(async (_sid: string, path: string) => ({ entries: map[path] ?? [] }))
}

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.setState({ activeId: 's1' })
  vi.clearAllMocks()
})

describe('FileExplorer', () => {
  it('renders root entries after load (dirs before files)', async () => {
    setList({ '': [entry('src', 'dir'), entry('readme.md', 'file')] })
    render(<FileExplorer />)
    expect(await screen.findByText('src')).toBeInTheDocument()
    expect(screen.getByText('readme.md')).toBeInTheDocument()
    const texts = screen.getAllByText(/^(src|readme\.md)$/).map(e => e.textContent)
    expect(texts).toEqual(['src', 'readme.md'])
  })

  it('expanding a dir fetches and shows its children', async () => {
    setList({ '': [entry('src', 'dir')], src: [entry('app.tsx', 'file')] })
    render(<FileExplorer />)
    await userEvent.click(await screen.findByText('src'))
    expect(listMock).toHaveBeenCalledWith('s1', 'src')
    expect(await screen.findByText('app.tsx')).toBeInTheDocument()
  })

  it('clicking a file calls openViewer with the right path', async () => {
    setList({ '': [entry('readme.md', 'file')] })
    const openViewer = vi.fn()
    useForge.setState({ openViewer })
    render(<FileExplorer />)
    await userEvent.click(await screen.findByText('readme.md'))
    expect(openViewer).toHaveBeenCalledWith('s1', 'readme.md')
  })

  it('rename flow calls fsMove with old and new path', async () => {
    setList({ '': [entry('old.txt', 'file')] })
    render(<FileExplorer />)
    const row = await screen.findByText('old.txt')
    await userEvent.pointer({ keys: '[MouseRight]', target: row })
    await userEvent.click(screen.getByRole('menuitem', { name: 'Rename' }))
    const input = screen.getByRole('textbox')
    await userEvent.clear(input)
    await userEvent.type(input, 'new.txt{Enter}')
    expect(api.fsMove).toHaveBeenCalledWith('s1', 'old.txt', 'new.txt')
  })

  it('delete flow shows ConfirmDialog and confirm calls fsDelete', async () => {
    setList({ '': [entry('gone.txt', 'file')] })
    render(<FileExplorer />)
    const row = await screen.findByText('gone.txt')
    await userEvent.pointer({ keys: '[MouseRight]', target: row })
    await userEvent.click(screen.getByRole('menuitem', { name: 'Delete' }))
    expect(screen.getByText(/permanently delete/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(api.fsDelete).toHaveBeenCalledWith('s1', 'gone.txt')
  })
})
