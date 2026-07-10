import { useState } from 'react'
import { ApiError } from '../api'
import type { Project } from '../protocol'
import { useForge, type SessionState } from '../state/store'
import ConfirmDialog from './ConfirmDialog'
import s from './Sidebar.module.css'

export function groupSessions(
  projects: Project[],
  order: string[],
  sessions: Record<string, SessionState>,
): { byProject: Record<string, string[]>; adhoc: string[]; archived: string[] } {
  const ids = new Set(projects.map(p => p.id))
  const byProject: Record<string, string[]> = Object.fromEntries(projects.map(p => [p.id, []]))
  const adhoc: string[] = []
  const archived: string[] = []
  for (const id of order) {
    const { projectId, archived: isArchived } = sessions[id].stream
    if (isArchived) archived.push(id)
    else if (projectId && ids.has(projectId)) byProject[projectId].push(id)
    else adhoc.push(id)
  }
  return { byProject, adhoc, archived }
}

function Dot({ active, busy }: { active: boolean; busy: boolean }) {
  return <span className={s.dot} data-state={active ? 'active' : busy ? 'busy' : 'idle'} />
}

export default function Sidebar() {
  const projects = useForge(st => st.projects)
  const order = useForge(st => st.order)
  const sessions = useForge(st => st.sessions)
  const activeId = useForge(st => st.activeId)
  const setActive = useForge(st => st.setActive)
  const openDialog = useForge(st => st.openDialog)
  const newSessionInProject = useForge(st => st.newSessionInProject)
  const archiveSession = useForge(st => st.archiveSession)
  const unarchiveSession = useForge(st => st.unarchiveSession)
  const deleteSession = useForge(st => st.deleteSession)

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set(['__archived__']))
  const [rowError, setRowError] = useState<{ id: string; msg: string } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const toggle = (key: string) =>
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const failSoft = (id: string) => (e: unknown) => {
    const msg = e instanceof ApiError && e.status === 409
      ? 'Session is running — cancel it first'
      : 'Action failed'
    setRowError({ id, msg })
    setTimeout(() => setRowError(cur => (cur?.id === id ? null : cur)), 4000)
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
          <button className={s.rowMain} onClick={() => setActive(id)}>
            <Dot active={id === activeId} busy={st.status !== 'idle'} />
            <span className={s.rowName}>{st.name}</span>
          </button>
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

  return (
    <nav className={s.sidebar}>
      <div className={s.label}>PROJECTS</div>

      {projects.map(p => (
        <section key={p.id}>
          <button className={s.sectionHeader} onClick={() => toggle(p.id)}>
            <span className={s.chevron}>{collapsed.has(p.id) ? '▸' : '▾'}</span>
            {p.name}
          </button>
          {!collapsed.has(p.id) && (
            <>
              {byProject[p.id].map(id => row(id, false))}
              <button className={s.plusRow} aria-label={`New session in ${p.name}`}
                      onClick={() => void newSessionInProject(p.id).catch(failSoft(p.id))}>
                ＋ new session
              </button>
            </>
          )}
        </section>
      ))}

      <div className={s.label}>AD-HOC</div>
      {adhoc.map(id => row(id, false))}
      <button className={s.plusRow} aria-label="New ad-hoc session"
              onClick={() => openDialog('new-session')}>
        ＋ new session…
      </button>

      <button className={s.newProject} aria-label="New project"
              onClick={() => openDialog('new-project')}>
        ＋ New project…
      </button>

      {archived.length > 0 && (
        <section className={s.archivedSection}>
          <button className={s.sectionHeader} onClick={() => toggle('__archived__')}>
            <span className={s.chevron}>{collapsed.has('__archived__') ? '▸' : '▾'}</span>
            ARCHIVED ({archived.length})
          </button>
          {!collapsed.has('__archived__') && archived.map(id => row(id, true))}
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
    </nav>
  )
}
