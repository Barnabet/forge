import { useEffect } from 'react'
import ChatStream from './components/ChatStream'
import Composer from './components/Composer'
import DetailDrawer from './components/DetailDrawer'
import NewProjectDialog from './components/NewProjectDialog'
import NewSessionDialog from './components/NewSessionDialog'
import Sidebar from './components/Sidebar'
import TopBar from './components/TopBar'
import { cursors, useForge } from './state/store'
import { startWs } from './ws'
import s from './App.module.css'

function wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}

export default function App() {
  useEffect(() => {
    // Hydrate backfills every session's stream over REST; never let a rejection
    // (e.g. engine down at boot) escape as an unhandled promise or crash the app.
    const runHydrate = () =>
      void useForge.getState().hydrate().catch(err => console.error('hydrate failed', err))
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

  return (
    <div className={s.frame}>
      {!sidebarCollapsed && <Sidebar />}
      <div className={s.rightCol}>
        <TopBar />
        {connection !== 'open' && <div className={s.connBanner}>reconnecting…</div>}
        <div className={s.main}>
          {activeId ? (
            <>
              <div className={s.chatCol}>
                <ChatStream />
                <Composer />
              </div>
              <DetailDrawer />
            </>
          ) : (
            <div className={s.empty}>No session — create one from the sidebar</div>
          )}
        </div>
      </div>
      {dialog === 'new-session' && <NewSessionDialog />}
      {dialog === 'new-project' && <NewProjectDialog />}
    </div>
  )
}
