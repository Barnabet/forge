import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, type WorkspaceStatus as WorkspaceStatusData } from '../api'
import { formatLastTs } from '../lib/time'
import { useForge } from '../state/store'
import s from './WorkspaceStatus.module.css'

const REFRESH_MS = 4000

// A change is "foreign" to the active session when it came from outside the
// tree (external) or from a different peer session on the same live files.
function isForeign(
  a: WorkspaceStatusData['recent_activity'][number], activeId: string,
): boolean {
  return a.origin === 'external'
    || (a.session_id !== null && a.session_id !== activeId)
}

export default function WorkspaceStatus() {
  const activeId = useForge(st => st.activeId)
  const [status, setStatus] = useState<WorkspaceStatusData | null>(null)
  const [open, setOpen] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // Snapshot fetch: refresh when the active session changes, when the panel
  // opens, and periodically while open. Failures degrade silently (the pill
  // just keeps its last snapshot, or stays hidden). A generation guard drops
  // responses that arrive after the active session has moved on.
  useEffect(() => {
    if (!activeId) { setStatus(null); return }
    let alive = true
    const load = () => {
      api.workspaceStatus(activeId).then(
        data => { if (alive) setStatus(data) },
        () => { /* silent: keep last snapshot */ },
      )
    }
    load()
    const poll = open ? window.setInterval(load, REFRESH_MS) : undefined
    return () => { alive = false; if (poll) window.clearInterval(poll) }
  }, [activeId, open])

  // Close on Escape and on click outside (mirrors ProjectsMenu).
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (panelRef.current?.contains(t)) return
      if (btnRef.current?.contains(t)) return
      setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [open])

  if (!activeId || !status || !Array.isArray(status.sessions)) return null

  const peers = status.sessions.filter(x => !x.archived && x.id !== activeId)
  const foreign = (status.recent_activity ?? []).filter(a => isForeign(a, activeId))
  const external = (status.last_external_paths ?? []).length > 0

  // Solo state with no foreign activity creates no clutter.
  if (peers.length === 0 && foreign.length === 0) return null

  // Peer count includes the active session for a natural "N sessions" read.
  const sessionCount = peers.length + 1
  const changeCount = foreign.length

  const rect = open ? btnRef.current?.getBoundingClientRect() : undefined
  const top = rect ? rect.bottom + 6 : 52
  const pos = rect
    ? { top, right: Math.max(12, window.innerWidth - rect.right) }
    : undefined

  return (
    <>
      <button
        ref={btnRef}
        className={s.pill}
        data-external={external || undefined}
        aria-expanded={open}
        aria-controls="workspace-status-panel"
        aria-label={
          `Shared workspace: ${sessionCount} sessions`
          + (changeCount > 0 ? `, ${changeCount} recent changes` : '')
          + (external ? ', external changes' : '')
        }
        onClick={() => setOpen(o => !o)}
      >
        <span className={s.dot} aria-hidden="true" />
        <span>{sessionCount} sessions</span>
        {changeCount > 0 && (
          <>
            <span className={s.sep} aria-hidden="true">·</span>
            <span>{changeCount} {changeCount === 1 ? 'change' : 'changes'}</span>
          </>
        )}
      </button>
      {open && createPortal(
        <div
          ref={panelRef}
          id="workspace-status-panel"
          className={s.panel}
          style={pos}
          role="dialog"
          aria-label="Shared workspace status"
        >
          <p className={s.explain}>All sessions edit the same live files.</p>

          <div className={s.sectionLabel}>SESSIONS</div>
          <ul className={s.list}>
            <li className={s.peer}>
              <span className={s.peerDot} data-busy={
                status.sessions.find(x => x.id === activeId)?.busy || undefined} />
              <span className={s.peerName}>
                {status.sessions.find(x => x.id === activeId)?.name ?? 'This session'}
              </span>
              <span className={s.peerTag}>this</span>
            </li>
            {peers.map(p => (
              <li key={p.id} className={s.peer}>
                <span className={s.peerDot} data-busy={p.busy || undefined} />
                <span className={s.peerName}>{p.name}</span>
                <span className={s.peerMeta}>
                  {p.busy ? 'running' : p.status}
                </span>
              </li>
            ))}
          </ul>

          {foreign.length > 0 && (
            <>
              <div className={s.sectionLabel}>RECENT ACTIVITY</div>
              <ul className={s.list}>
                {foreign.map(a => (
                  <li
                    key={a.seq}
                    className={s.activity}
                    data-external={a.origin === 'external' || undefined}
                  >
                    <span className={s.actWho}>{a.author}</span>
                    <span className={s.actAction}>{a.action}</span>
                    <span className={s.actPaths} title={a.paths.join(', ')}>
                      {a.paths.join(', ')}
                    </span>
                    <span className={s.actTime}>{formatLastTs(a.timestamp)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>,
        document.body,
      )}
    </>
  )
}
