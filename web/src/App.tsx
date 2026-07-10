import { useEffect } from 'react'
import ChatStream from './components/ChatStream'
import Composer from './components/Composer'
import DetailDrawer from './components/DetailDrawer'
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
    const st = useForge.getState()
    void st.hydrate()
    const stop = startWs({
      url: wsUrl(),
      cursors: () => cursors(useForge.getState()),
      onEvent: e => useForge.getState().applyEvent(e),
      onStatus: c => useForge.getState().setConnection(c),
    })
    const health = setInterval(() => void useForge.getState().refreshHealth(), 15_000)
    return () => { stop(); clearInterval(health) }
  }, [])

  const connection = useForge(st => st.connection)

  return (
    <div className={s.frame}>
      <TopBar />
      {connection !== 'open' && (
        <div className={s.connBanner}>reconnecting…</div>
      )}
      <div className={s.main}>
        <div className={s.chatCol}>
          <ChatStream />
          <Composer />
        </div>
        <DetailDrawer />
      </div>
    </div>
  )
}
