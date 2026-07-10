import { useState } from 'react'
import { useForge } from '../state/store'
import s from './TodoStrip.module.css'

const GLYPH = { pending: '○', in_progress: '◐', completed: '✓' } as const

export default function TodoStrip() {
  const [expanded, setExpanded] = useState(false)
  const todos = useForge(st =>
    st.activeId ? st.sessions[st.activeId].stream.todos : undefined)

  if (!todos || todos.length === 0) return null

  const done = todos.filter(t => t.status === 'completed').length
  const current = todos.find(t => t.status === 'in_progress')

  return (
    <div className={s.strip}>
      <button className={s.summary} onClick={() => setExpanded(e => !e)}>
        <span className={s.progress}>◐ {done}/{todos.length}</span>
        {!expanded && current && <span className={s.current}>{current.text}</span>}
        <span className={s.chevron}>{expanded ? '▾' : '▸'}</span>
      </button>
      {expanded && (
        <ul className={s.list}>
          {todos.map((t, i) => (
            <li key={i} className={s.item} data-status={t.status}>
              <span className={s.glyph}>{GLYPH[t.status]}</span>
              <span className={s.text}>{t.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
