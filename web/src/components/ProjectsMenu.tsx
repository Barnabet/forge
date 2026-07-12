import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ApiError, type IndexStatus } from '../api'
import type { SessionStream } from '../state/reducer'
import { formatLastTs } from '../lib/time'
import { useForge } from '../state/store'
import ConfirmDialog from './ConfirmDialog'
import { groupSessions } from './Sidebar'
import s from './ProjectsMenu.module.css'

type DotState = 'running' | 'queued' | 'problem' | 'unread' | 'idle'

const DOT_LABEL: Record<DotState, string> = {
  running: 'Running',
  queued: 'On hold',
  problem: 'Last run failed',
  unread: 'New response',
  idle: 'Idle',
}

// Resolve the session dot state with strict precedence:
//   1. agent/subagent waiting in a queue → queued (yellow)
//   2. active work                       → running (green)
//   3. idle run ended error/interrupted  → problem (red)
//   4. idle unread successful completion → unread (white/black)
//   5. otherwise                         → idle (neutral)
// Selection never changes the dot color. A blocked subagent is also on hold:
// it is waiting for the shared write lock rather than a scheduler slot.
function dotState(st: SessionStream): DotState {
  const workerOnHold = st.subagents?.workers.some(
    worker => worker.state === 'queued' || worker.state === 'blocked') ?? false
  if (st.status === 'queued' || st.status === 'attention' || workerOnHold) return 'queued'
  if (st.status === 'running') return 'running'
  if (st.lastRunReason === 'error' || st.lastRunReason === 'interrupted') return 'problem'
  if (st.unread) return 'unread'
  return 'idle'
}

function Dot({ state }: { state: DotState }) {
  const label = DOT_LABEL[state]
  return <span className={s.dot} data-state={state} title={label} aria-label={label} />
}

// A small trailing marker in the header row: a ✓ once a project's workspace is
// vectorized, an error tick if indexing failed, nothing while in flight (the
// bar underneath conveys progress).
function IndexIndicator({ status }: { status?: IndexStatus }) {
  if (!status || status.state === 'indexing') return null
  const ready = status.state === 'ready'
  return (
    <span className={s.indexMark} data-state={status.state}
          title={ready ? 'Workspace vectorized' : 'Vectorization failed'}
          aria-label={ready ? 'Workspace vectorized' : 'Vectorization failed'}>
      {ready ? '✓' : '!'}
    </span>
  )
}

// A thin determinate progress bar under the header row while a project's
// workspace is being vectorized. Indeterminate slide until the chunk total is
// known (total === 0).
function IndexBar({ status }: { status?: IndexStatus }) {
  if (!status || status.state !== 'indexing') return null
  const pct = status.total > 0 ? Math.round((status.done / status.total) * 100) : 0
  const indeterminate = status.total === 0
  return (
    <div className={s.indexBar} role="progressbar"
         aria-valuenow={indeterminate ? undefined : pct}
         aria-label="Vectorizing workspace">
      <div className={indeterminate ? s.indexFillIndeterminate : s.indexFill}
           style={indeterminate ? undefined : { width: `${pct}%` }} />
    </div>
  )
}

export default function ProjectsMenu({ onClose, anchorRef }: {
  onClose: () => void
  anchorRef?: React.RefObject<HTMLElement | null>
}) {
  const projects = useForge(st => st.projects)
  const fileIndex = useForge(st => st.fileIndex)
  const order = useForge(st => st.order)
  const sessions = useForge(st => st.sessions)
  const activeId = useForge(st => st.activeId)
  const setActive = useForge(st => st.setActive)
  const openDialog = useForge(st => st.openDialog)
  const newSessionInProject = useForge(st => st.newSessionInProject)
  const archiveSession = useForge(st => st.archiveSession)
  const unarchiveSession = useForge(st => st.unarchiveSession)
  const deleteSession = useForge(st => st.deleteSession)

  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem('forge.projectsMenu.collapsed')
      if (raw) return new Set(JSON.parse(raw) as string[])
    } catch { /* ignore malformed */ }
    return new Set(['__archived__'])
  })
  const [rowError, setRowError] = useState<{ id: string; msg: string } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const panelRef = useRef<HTMLDivElement>(null)

  // Close on Escape and on click outside (mirrors Modal.tsx patterns).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      // Ignore clicks inside the panel or on the anchor (the header button
      // toggles the menu itself; closing here would double-fire).
      if (panelRef.current?.contains(t)) return
      if (anchorRef?.current?.contains(t)) return
      onClose()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [onClose, anchorRef])

  const toggle = (key: string) =>
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      localStorage.setItem('forge.projectsMenu.collapsed', JSON.stringify([...next]))
      return next
    })

  const failSoft = (id: string) => (e: unknown) => {
    const msg = e instanceof ApiError && e.status === 409
      ? 'Session is running — cancel it first'
      : 'Action failed'
    setRowError({ id, msg })
    setTimeout(() => setRowError(cur => (cur?.id === id ? null : cur)), 4000)
  }

  const select = (id: string) => {
    setActive(id)
    onClose()
  }

  const { byProject, adhoc, archived } = groupSessions(projects, order, sessions)
  // Guard: a remote session_deleted can drop the target while its confirm
  // dialog is open. Render the dialog only while the session still exists.
  const target = confirmDelete ? sessions[confirmDelete] : undefined

  const row = (id: string, isArchived: boolean) => {
    const st = sessions[id].stream
    return (
      <div key={id} className={s.rowWrap}>
        <div className={id === activeId ? s.rowActive : s.row}>
          <button className={s.rowMain} onClick={() => select(id)}>
            <Dot state={dotState(st)} />
            <span className={s.rowName}>{st.name}</span>
          </button>
          <span className={s.rowTime}>{formatLastTs(st.lastTs)}</span>
          <span className={s.rowActions}>
            {isArchived ? (
              <>
                <button className={s.action} aria-label={`Unarchive ${st.name}`}
                        title="Unarchive"
                        onClick={() => void unarchiveSession(id).catch(failSoft(id))}>⤴</button>
                <button className={s.actionDanger} aria-label={`Delete ${st.name}`}
                        title="Delete" onClick={() => setConfirmDelete(id)}>✕</button>
              </>
            ) : (
              <button className={s.action} aria-label={`Archive ${st.name}`}
                      title="Archive"
                      onClick={() => void archiveSession(id).catch(failSoft(id))}>⌫</button>
            )}
          </span>
        </div>
        {rowError?.id === id && <div className={s.rowError}>{rowError.msg}</div>}
      </div>
    )
  }

  // Fixed positioning from the anchor rect: the sidebar clips overflow, so the
  // panel is portaled to <body> and placed just below the header button.
  const rect = anchorRef?.current?.getBoundingClientRect()
  const top = rect ? rect.bottom + 6 : 52
  const pos = {
    top,
    left: rect ? rect.left + 8 : 8,
    maxHeight: Math.min(window.innerHeight * 0.7, window.innerHeight - top - 12),
  }

  return createPortal(
    <div ref={panelRef} className={s.menu} style={pos} role="menu">
      <button className={s.newProject} aria-label="New project"
              onClick={() => openDialog('new-project')}>
        ＋ New project…
      </button>

      {projects.map(p => (
        <section key={p.id}>
          <div className={s.sectionHeaderRow}>
            <button className={s.sectionHeader} onClick={() => toggle(p.id)}>
              <span className={s.chevron}>{collapsed.has(p.id) ? '▸' : '▾'}</span>
              {p.name}
            </button>
            <IndexIndicator status={fileIndex[p.id]} />
            <button className={s.headerAdd} aria-label={`New session in ${p.name}`}
                    title="New session"
                    onClick={() => void newSessionInProject(p.id).catch(failSoft(p.id))}>
              ＋
            </button>
          </div>
          <IndexBar status={fileIndex[p.id]} />
          {!collapsed.has(p.id) && (
            <div className={s.sectionBody}>
              {byProject[p.id].map(id => row(id, false))}
            </div>
          )}
        </section>
      ))}

      <div className={s.label}>AD-HOC</div>
      <div className={s.sectionBody}>
        {adhoc.map(id => row(id, false))}
        <button className={s.plusRow} aria-label="New ad-hoc session"
                onClick={() => openDialog('new-session')}>
          ＋ new session…
        </button>
      </div>

      {archived.length > 0 && (
        <section className={s.archivedSection}>
          <button className={s.sectionHeader} onClick={() => toggle('__archived__')}>
            <span className={s.chevron}>{collapsed.has('__archived__') ? '▸' : '▾'}</span>
            ARCHIVED ({archived.length})
          </button>
          {!collapsed.has('__archived__') && (
            <div className={s.sectionBody}>{archived.map(id => row(id, true))}</div>
          )}
        </section>
      )}

      {target && (
        <ConfirmDialog
          title="Delete session"
          body={`This will permanently delete "${target.stream.name}" and its history.`}
          confirmLabel="Delete"
          onCancel={() => setConfirmDelete(null)}
          onConfirm={() => {
            const id = target.id
            setConfirmDelete(null)
            void deleteSession(id).catch(failSoft(id))
          }}
        />
      )}
    </div>,
    document.body,
  )
}
