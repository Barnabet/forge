import { useForge } from '../state/store'
import s from './TopBar.module.css'

function abbreviate(cwd: string): string {
  return cwd.replace(/^\/(Users|home)\/[^/]+/, '~')
}

export default function TopBar() {
  const order = useForge(st => st.order)
  const sessions = useForge(st => st.sessions)
  const activeId = useForge(st => st.activeId)

  const queued = order.filter(id => sessions[id].stream.status === 'queued').length
  const cwd = activeId ? sessions[activeId].stream.cwd : ''

  return (
    <header className={s.bar}>
      <div className={s.brand}>
        <div className={s.logo} />
        <span className={s.name}>Forge</span>
      </div>
      <div className={s.right}>
        {queued > 0 && (
          <span className={s.queuePill}>
            <span className={s.queueDot} />
            {queued} queued
          </span>
        )}
        <span className={s.cwd}>{cwd ? abbreviate(cwd) : ''}</span>
      </div>
    </header>
  )
}
