import { useEffect, useRef } from 'react'
import Markdown from 'react-markdown'
import { api } from '../api'
import { useForge } from '../state/store'
import ApprovalGate from './ApprovalGate'
import ToolCard from './ToolCard'
import s from './ChatStream.module.css'

export default function ChatStream() {
  const activeId = useForge(st => st.activeId)
  const session = useForge(st => (st.activeId ? st.sessions[st.activeId] : undefined))
  const openDrawer = useForge(st => st.openDrawer)
  const scroller = useRef<HTMLDivElement>(null)

  const itemCount = session?.stream.items.length ?? 0
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [itemCount, activeId])

  if (!session) return <div ref={scroller} className={s.scroller} />
  const { items, status, steps } = session.stream

  const statusText =
    status === 'running' ? `Working · step ${steps}`
    : status === 'attention' ? `Waiting on approval · step ${steps}`
    : status === 'queued' ? 'Queued — waiting for a slot'
    : null

  return (
    <div ref={scroller} className={s.scroller}>
      <div className={s.column}>
        {items.map((item, i) => {
          switch (item.kind) {
            case 'user':
              return <div key={i} className={s.userRow}><div className={s.userBubble}>{item.text}</div></div>
            case 'prose':
              return <div key={i} className={s.prose}><Markdown>{item.text}</Markdown></div>
            case 'tool':
              return <ToolCard key={i} item={item} onOpenPanel={idx => void openDrawer(idx)} />
            case 'gate':
              return (
                <ApprovalGate
                  key={i}
                  item={item}
                  onResolve={(decision, always) =>
                    void api.resolveApproval(session.id, item.callId, decision, always)}
                />
              )
            case 'error':
              return <div key={i} className={s.errorLine}>{item.message}</div>
            case 'info':
              return <div key={i} className={s.infoLine}>{item.text}</div>
            case 'compacted':
              return <div key={i} className={s.compacted}>· context compacted ·</div>
          }
        })}
        {statusText && (
          <div className={s.statusLine}>
            <span className={s.statusDot} />
            {statusText}
          </div>
        )}
      </div>
    </div>
  )
}
