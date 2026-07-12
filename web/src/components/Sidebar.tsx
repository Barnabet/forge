import { useRef, useState } from 'react'
import type { Project } from '../protocol'
import {
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
  useForge,
  type SessionState,
} from '../state/store'
import FileExplorer from './FileExplorer'
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
  // Most recent activity first; ties keep insertion order (sort is stable).
  const sorted = [...order].sort(
    (a, b) => sessions[b].stream.lastTs - sessions[a].stream.lastTs)
  for (const id of sorted) {
    const { projectId, archived: isArchived } = sessions[id].stream
    if (isArchived) archived.push(id)
    else if (projectId && ids.has(projectId)) byProject[projectId].push(id)
    else adhoc.push(id)
  }
  return { byProject, adhoc, archived }
}

export default function Sidebar({ collapsed }: { collapsed: boolean }) {
  const activeId = useForge(st => st.activeId)
  const sidebarWidth = useForge(st => st.sidebarWidth)
  const setSidebarWidth = useForge(st => st.setSidebarWidth)
  const dragging = useRef(false)
  const [resizing, setResizing] = useState(false)

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault()
    dragging.current = true
    setResizing(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev: PointerEvent) => {
      if (dragging.current) setSidebarWidth(ev.clientX)
    }
    const onUp = () => {
      dragging.current = false
      setResizing(false)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  return (
    <nav
      className={s.sidebar}
      data-collapsed={collapsed || undefined}
      data-resizing={resizing || undefined}
      aria-hidden={collapsed || undefined}
      style={{ width: collapsed ? 0 : sidebarWidth }}
    >
      <div className={s.inner} style={{ width: sidebarWidth }}>
        {activeId ? (
          <div className={s.explorerSlot}>
            <FileExplorer />
          </div>
        ) : (
          <div className={s.emptyExplorer}>Select a session to browse files</div>
        )}
      </div>
      {!collapsed && (
        <div
          className={s.resizer}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize sidebar"
          aria-valuemin={SIDEBAR_MIN_WIDTH}
          aria-valuemax={SIDEBAR_MAX_WIDTH}
          aria-valuenow={sidebarWidth}
          onPointerDown={startResize}
        />
      )}
    </nav>
  )
}
