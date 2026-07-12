import { useEffect } from 'react'
import ChatStream from './components/ChatStream'
import Composer from './components/Composer'
import FloatingWindow from './components/FloatingWindow'
import FileViewer from './components/FileViewer'
import Lightbox from './components/Lightbox'
import NewProjectDialog from './components/NewProjectDialog'
import NewSessionDialog from './components/NewSessionDialog'
import Sidebar from './components/Sidebar'
import TerminalDock from './components/TerminalDock'
import TopBar from './components/TopBar'
import { cursors, useForge } from './state/store'
import { startWs } from './ws'
import s from './App.module.css'

function wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}

// Render a file path as a mono title: dimmed directory prefix, bright basename,
// and a small uppercase extension stamp (omitted when there is no extension).
function ViewerTitle({ path }: { path: string }) {
  const slash = path.lastIndexOf('/')
  const dir = slash < 0 ? '' : path.slice(0, slash + 1)
  const base = slash < 0 ? path : path.slice(slash + 1)
  const dot = base.lastIndexOf('.')
  const ext = dot > 0 ? base.slice(dot + 1) : ''
  return (
    <span className={s.viewerTitle}>
      {dir && <span className={s.viewerDir}>{dir}</span>}
      <span className={s.viewerBase}>{base}</span>
      {ext && <span className={s.viewerExt}>{ext}</span>}
    </span>
  )
}

export default function App() {
  useEffect(() => {
    // Hydrate backfills every session's stream over REST; never let a rejection
    // (e.g. engine down at boot) escape as an unhandled promise or crash the app.
    const runHydrate = () =>
      void useForge.getState().hydrate()
        .then(() => {
          // Reconcile terminals for every resident session after the stream
          // backfill settles; each read starts from the offset we already hold.
          const st = useForge.getState()
          return Promise.all(st.order.map(id => st.hydrateTerminals(id)))
        })
        .catch(err => console.error('hydrate failed', err))
    runHydrate()
    const stop = startWs({
      url: wsUrl(),
      cursors: () => cursors(useForge.getState()),
      onEvent: e => useForge.getState().applyEvent(e),
      onStatus: c => {
        useForge.getState().setConnection(c)
        // Re-hydrate on every (re)connect so each open backfills any gap and, when
        // the engine recovers, acts as the natural retry for a failed boot hydrate.
        if (c === 'open') runHydrate()
      },
    })
    const health = setInterval(() => void useForge.getState().refreshHealth(), 15_000)
    return () => { stop(); clearInterval(health) }
  }, [])

  const connection = useForge(st => st.connection)
  const activeId = useForge(st => st.activeId)
  const dialog = useForge(st => st.dialog)
  const sidebarCollapsed = useForge(st => st.sidebarCollapsed)
  const terminalDockOpen = useForge(st => (activeId ? st.terminalDockOpen[activeId] : false))
  const viewers = useForge(st => st.viewers)
  const closeViewer = useForge(st => st.closeViewer)
  const focusViewer = useForge(st => st.focusViewer)
  const maxViewerZ = viewers.reduce((m, v) => Math.max(m, v.z), 0)

  return (
    <div className={s.frame}>
      <Sidebar collapsed={sidebarCollapsed} />
      <div className={s.rightCol}>
        <TopBar />
        {connection !== 'open' && <div className={s.connBanner}>reconnecting…</div>}
        <div className={s.main}>
          {activeId ? (
            <div className={s.chatCol}>
              <ChatStream />
              <Composer />
            </div>
          ) : (
            <div className={s.empty}>No session — create one from the sidebar</div>
          )}
          {activeId && terminalDockOpen && <TerminalDock />}
        </div>
      </div>
      <Lightbox />
      {dialog === 'new-session' && <NewSessionDialog />}
      {dialog === 'new-project' && <NewProjectDialog />}
      {viewers.map((v, i) => (
        <FloatingWindow
          key={v.id}
          title={<ViewerTitle path={v.path} />}
          ariaLabel={v.path}
          focused={v.z === maxViewerZ}
          zIndex={100 + v.z}
          initialX={120 + i * 32}
          initialY={120 + i * 32}
          onClose={() => closeViewer(v.id)}
          onFocus={() => focusViewer(v.id)}
        >
          <FileViewer sid={v.sid} path={v.path} />
        </FloatingWindow>
      ))}
    </div>
  )
}
