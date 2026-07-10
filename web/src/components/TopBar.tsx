import { useForge } from '../state/store'
import s from './TopBar.module.css'

function abbreviate(cwd: string): string {
  return cwd.replace(/^\/(Users|home)\/[^/]+/, '~')
}

export default function TopBar() {
  const order = useForge(st => st.order)
  const sessions = useForge(st => st.sessions)
  const activeId = useForge(st => st.activeId)
  const setActive = useForge(st => st.setActive)
  const newSession = useForge(st => st.newSession)

  const queued = order.filter(id => sessions[id].stream.status === 'queued').length
  const cwd = activeId ? sessions[activeId].stream.cwd : ''

  return (
    <header className={s.bar}>
      <div className={s.brand}>
        <div className={s.logo} />
        <span className={s.name}>Forge</span>
      </div>
      <div className={s.tabs} role="tablist">
        {order.map(id => {
          const st = sessions[id].stream
          const active = id === activeId
          const busy = st.status !== 'idle'
          return (
            <button
              key={id}
              role="tab"
              aria-selected={active}
              className={active ? s.tabActive : s.tab}
              onClick={() => setActive(id)}
            >
              <span
                className={s.dot}
                data-state={active ? 'active' : busy ? 'busy' : 'idle'}
              />
              {st.name}
            </button>
          )
        })}
        <button className={s.plus} aria-label="New session" onClick={() => void newSession()}>
          +
        </button>
      </div>
      <div className={s.right}>
        {queued > 0 && (
          <span className={s.queuePill}>
            <span className={s.queueDot} />
            {queued} queued
          </span>
        )}
        <span className={s.cwd}>{abbreviate(cwd)}</span>
      </div>
    </header>
  )
}
