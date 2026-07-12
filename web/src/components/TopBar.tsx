import { useRef, useState } from 'react'
import { useForge } from '../state/store'
import ConfigDrawer from './ConfigDrawer'
import ProjectsMenu from './ProjectsMenu'
import SubagentPanel from './SubagentPanel'
import WorkspaceStatus from './WorkspaceStatus'
import s from './TopBar.module.css'

function abbreviate(cwd: string): string {
  return cwd.replace(/^\/(Users|home)\/[^/]+/, '~')
}

export default function TopBar() {
  const order = useForge(st => st.order)
  const sessions = useForge(st => st.sessions)
  const activeId = useForge(st => st.activeId)
  const projects = useForge(st => st.projects)
  const toggleSidebar = useForge(st => st.toggleSidebar)
  const newSessionInProject = useForge(st => st.newSessionInProject)
  const openDialog = useForge(st => st.openDialog)
  const terminals = activeId ? sessions[activeId].stream.terminals.order.length : 0
  const terminalDockOpen = useForge(st => (activeId ? st.terminalDockOpen[activeId] : false))
  const setTerminalDockOpen = useForge(st => st.setTerminalDockOpen)

  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsRef = useRef<HTMLButtonElement>(null)

  const [projectsOpen, setProjectsOpen] = useState(false)
  const brandRef = useRef<HTMLButtonElement>(null)

  const queued = order.filter(id => sessions[id].stream.status === 'queued').length
  const cwd = activeId ? sessions[activeId].stream.cwd : ''
  const memoryState = activeId ? sessions[activeId].stream.memoryState : null

  // Active session's context label: its project name, 'Ad-hoc' when it has no
  // known project, or 'No session' when nothing is active.
  const active = activeId ? sessions[activeId] : undefined
  const projectId = active?.stream.projectId
  const project = projectId ? projects.find(p => p.id === projectId) : undefined
  const contextLabel = !active ? 'No session' : project ? project.name : 'Ad-hoc'

  const memoryLabel = {
    running: 'memory…',
    written: 'memory updated',
    unchanged: 'memory unchanged',
    error: 'memory failed',
  } as const

  return (
    <header className={s.bar}>
      <button className={s.sidebarToggle} aria-label="Toggle sidebar" onClick={toggleSidebar}>
        ☰
      </button>
      <button
        ref={brandRef}
        className={s.brand}
        aria-label="Toggle projects menu"
        aria-expanded={projectsOpen}
        onClick={() => setProjectsOpen(o => !o)}
      >
        <span className={s.name}>{contextLabel}</span>
        <span className={s.chevron} data-open={projectsOpen || undefined}>▾</span>
      </button>
      {projectsOpen && (
        <ProjectsMenu onClose={() => setProjectsOpen(false)} anchorRef={brandRef} />
      )}
      <button
        className={s.newSession}
        aria-label={project ? `New session in ${project.name}` : 'New session'}
        title="New session"
        onClick={() =>
          project
            ? void newSessionInProject(project.id)
            : openDialog('new-session')
        }
      >
        ＋
      </button>
      <SubagentPanel />
      <div className={s.right}>
        <WorkspaceStatus />
        {queued > 0 && (
          <span className={s.queuePill}>
            <span className={s.queueDot} />
            {queued} queued
          </span>
        )}
        {memoryState && (
          <span className={`${s.memoryPill} ${s[memoryState]}`}>
            <span className={s.memoryDot} />
            {memoryLabel[memoryState]}
          </span>
        )}
        <span className={s.cwd}>{cwd ? abbreviate(cwd) : ''}</span>
        {activeId && (
          <button
            className={s.settings}
            aria-label="Toggle terminals"
            aria-pressed={terminalDockOpen}
            title="Terminals"
            onClick={() => activeId && setTerminalDockOpen(activeId, !terminalDockOpen)}
          >
            ▸_{terminals > 0 ? ` ${terminals}` : ''}
          </button>
        )}
        <button
          ref={settingsRef}
          className={s.settings}
          aria-label="Settings"
          aria-expanded={settingsOpen}
          title="Settings"
          onClick={() => setSettingsOpen(o => !o)}
        >
          ⚙
        </button>
        {settingsOpen && (
          <ConfigDrawer onClose={() => setSettingsOpen(false)} anchorRef={settingsRef} />
        )}
      </div>
    </header>
  )
}
