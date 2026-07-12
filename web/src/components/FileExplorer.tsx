import { useCallback, useEffect, useState, type DragEvent, type ReactNode } from 'react'
import { api, type FsEntry } from '../api'
import { useIconProvider } from '../lib/icons'
import { resolveUiTheme } from '../lib/theme'
import { useForge } from '../state/store'
import ConfirmDialog from './ConfirmDialog'
import s from './FileExplorer.module.css'

const DRAG_TYPE = 'application/x-forge-path'

const join = (dir: string, name: string) => (dir ? `${dir}/${name}` : name)
const basename = (path: string) => path.slice(path.lastIndexOf('/') + 1)
const parentOf = (path: string) => {
  const i = path.lastIndexOf('/')
  return i < 0 ? '' : path.slice(0, i)
}

type Editing =
  | { kind: 'file' | 'dir'; parent: string }
  | { kind: 'rename'; parent: string; path: string }

type Menu = { x: number; y: number; path: string; isDir: boolean; isRoot: boolean }

export default function FileExplorer() {
  const sid = useForge(st => st.activeId)
  const openViewer = useForge(st => st.openViewer)
  const iconTheme = useForge(st => st.iconTheme)
  const icons = useIconProvider(iconTheme)
  const iconMode = resolveUiTheme(useForge(st => st.uiTheme))

  const [children, setChildren] = useState<Record<string, FsEntry[]>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [menu, setMenu] = useState<Menu | null>(null)
  const [editing, setEditing] = useState<Editing | null>(null)
  const [editValue, setEditValue] = useState('')
  const [editError, setEditError] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<{ path: string; name: string } | null>(null)
  const [dragOver, setDragOver] = useState<string | null>(null)

  const loadDir = useCallback(async (path: string) => {
    if (!sid) return
    const { entries } = await api.fsList(sid, path)
    setChildren(prev => ({ ...prev, [path]: entries }))
  }, [sid])

  // Reset and reload when the active session changes.
  useEffect(() => {
    setChildren({})
    setExpanded(new Set())
    setMenu(null)
    setEditing(null)
    setConfirmDelete(null)
    setDragOver(null)
    void loadDir('')
  }, [sid, loadDir])

  // Dismiss the context menu on any outside click or Escape.
  useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenu(null) }
    window.addEventListener('click', close)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [menu])

  const toggle = (path: string) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else {
        next.add(path)
        if (!(path in children)) void loadDir(path)
      }
      return next
    })

  const refreshAll = () => {
    void loadDir('')
    for (const p of expanded) if (p) void loadDir(p)
  }

  const startNew = (kind: 'file' | 'dir', dir: string) => {
    setMenu(null)
    setExpanded(prev => new Set(prev).add(dir))
    if (!(dir in children)) void loadDir(dir)
    setEditing({ kind, parent: dir })
    setEditValue('')
    setEditError(false)
  }

  const startRename = (path: string) => {
    setMenu(null)
    setEditing({ kind: 'rename', parent: parentOf(path), path })
    setEditValue(basename(path))
    setEditError(false)
  }

  const commitEdit = async () => {
    if (!sid || !editing) return
    const value = editValue.trim()
    if (!value) { setEditing(null); return }
    try {
      if (editing.kind === 'rename') await api.fsMove(sid, editing.path, join(editing.parent, value))
      else if (editing.kind === 'dir') await api.fsMkdir(sid, join(editing.parent, value))
      else await api.fsTouch(sid, join(editing.parent, value))
      await loadDir(editing.parent)
      setExpanded(prev => new Set(prev).add(editing.parent))
      setEditing(null)
      setEditError(false)
    } catch {
      setEditError(true)
    }
  }

  const doDelete = async () => {
    if (!sid || !confirmDelete) return
    const { path } = confirmDelete
    setConfirmDelete(null)
    await api.fsDelete(sid, path)
    await loadDir(parentOf(path))
  }

  const handleDrop = async (targetDir: string, e: DragEvent) => {
    e.preventDefault()
    setDragOver(null)
    if (!sid) return
    const files = Array.from(e.dataTransfer.files)
    if (files.length > 0) {
      await api.fsUpload(sid, targetDir, files)
      setExpanded(prev => new Set(prev).add(targetDir))
      await loadDir(targetDir)
      return
    }
    const src = e.dataTransfer.getData(DRAG_TYPE)
    if (!src) return
    const srcParent = parentOf(src)
    // Never drop into itself, its own descendants, or its current parent.
    if (targetDir === src || targetDir === srcParent || targetDir.startsWith(`${src}/`)) return
    await api.fsMove(sid, src, join(targetDir, basename(src)))
    await Promise.all([loadDir(srcParent), loadDir(targetDir)])
  }

  const dropProps = (dir: string) => ({
    onDragOver: (e: DragEvent) => { e.preventDefault(); e.stopPropagation(); setDragOver(dir) },
    onDragLeave: (e: DragEvent) => { e.stopPropagation(); setDragOver(cur => (cur === dir ? null : cur)) },
    onDrop: (e: DragEvent) => { e.stopPropagation(); void handleDrop(dir, e) },
  })

  const onDragStart = (e: DragEvent, path: string) => {
    e.dataTransfer.setData(DRAG_TYPE, path)
    e.dataTransfer.effectAllowed = 'move'
  }

  const editRow = (chevron: string): ReactNode => (
    <div className={s.row}>
      <span className={s.chevron}>{chevron}</span>
      <input
        className={s.input}
        autoFocus
        value={editValue}
        data-error={editError || undefined}
        title={editError ? 'Name already exists or is invalid' : undefined}
        onChange={ev => { setEditValue(ev.target.value); setEditError(false) }}
        onClick={ev => ev.stopPropagation()}
        onKeyDown={ev => {
          if (ev.key === 'Enter') void commitEdit()
          else if (ev.key === 'Escape') { setEditing(null); setEditError(false) }
        }}
        onBlur={() => { if (!editError) setEditing(null) }}
      />
    </div>
  )

  const renderRow = (entry: FsEntry, dir: string): ReactNode => {
    const path = join(dir, entry.name)
    const isDir = entry.type === 'dir'
    const isOpen = expanded.has(path)
    const chevron = isDir ? (isOpen ? '▾' : '▸') : ''
    if (editing?.kind === 'rename' && editing.path === path)
      return <div key={path}>{editRow(chevron)}</div>
    return (
      <div key={path}>
        <div
          className={isDir ? s.rowDir : s.row}
          data-drop={dragOver === path ? 'over' : undefined}
          draggable
          onDragStart={e => onDragStart(e, path)}
          onClick={() => (isDir ? toggle(path) : openViewer(sid!, path))}
          onContextMenu={ev => {
            ev.preventDefault()
            ev.stopPropagation()
            setMenu({ x: ev.clientX, y: ev.clientY, path, isDir, isRoot: false })
          }}
          {...(isDir ? dropProps(path) : {})}
        >
          <span className={s.chevron}>{chevron}</span>
          {icons ? (
            <img
              className={s.icon}
              data-theme={iconTheme}
              src={isDir
                ? icons.folder(entry.name, isOpen, iconMode)
                : icons.file(entry.name, iconMode)}
              alt=""
              aria-hidden
            />
          ) : (
            <span className={s.icon} aria-hidden />
          )}
          <span className={isDir ? s.dirName : s.name}>{entry.name}</span>
        </div>
        {isDir && isOpen && <div className={s.guide}>{renderChildren(path)}</div>}
      </div>
    )
  }

  const renderChildren = (dir: string): ReactNode => {
    const entries = children[dir]
    if (!entries) return null
    const newRow = editing && editing.kind !== 'rename' && editing.parent === dir
      ? <div key="__new__">{editRow(editing.kind === 'dir' ? '▸' : '')}</div>
      : null
    return (
      <>
        {newRow}
        {entries.map(e => renderRow(e, dir))}
      </>
    )
  }

  if (!sid) return null

  return (
    <div className={s.explorer}>
      <div className={s.header}>
        <span className={s.headerLabel}>EXPLORER</span>
        <button className={s.refresh} title="Refresh" aria-label="Refresh" onClick={refreshAll}>⟳</button>
      </div>

      <div
        className={s.tree}
        data-drop={dragOver === '' ? 'over' : undefined}
        onContextMenu={ev => {
          ev.preventDefault()
          setMenu({ x: ev.clientX, y: ev.clientY, path: '', isDir: true, isRoot: true })
        }}
        onDragOver={e => { e.preventDefault(); setDragOver('') }}
        onDragLeave={() => setDragOver(cur => (cur === '' ? null : cur))}
        onDrop={e => void handleDrop('', e)}
      >
        {renderChildren('')}
      </div>

      {menu && (
        <div className={s.menu} style={{ left: menu.x, top: menu.y }} role="menu">
          {menu.isDir && (
            <button className={s.menuItem} role="menuitem" onClick={() => startNew('file', menu.path)}>New File</button>
          )}
          {menu.isDir && (
            <button className={s.menuItem} role="menuitem" onClick={() => startNew('dir', menu.path)}>New Folder</button>
          )}
          {!menu.isRoot && (
            <button className={s.menuItem} role="menuitem" onClick={() => startRename(menu.path)}>Rename</button>
          )}
          {!menu.isRoot && (
            <button className={s.menuItem} role="menuitem" onClick={() => setConfirmDelete({ path: menu.path, name: basename(menu.path) })}>Delete</button>
          )}
        </div>
      )}

      {confirmDelete && (
        <ConfirmDialog
          title={`Delete ${confirmDelete.name}?`}
          body={`This will permanently delete "${confirmDelete.name}".`}
          confirmLabel="Delete"
          onCancel={() => setConfirmDelete(null)}
          onConfirm={() => void doDelete()}
        />
      )}
    </div>
  )
}
