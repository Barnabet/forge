import { useEffect, useRef } from 'react'
import Markdown from 'react-markdown'
import { api } from '../api'
import { useForge } from '../state/store'
import type { StreamItem } from '../state/reducer'
import ApprovalGate from './ApprovalGate'
import ToolCard from './ToolCard'
import s from './ChatStream.module.css'

// Stable keys: the reducer splices items out of the middle (allowed gates,
// empty streaming prose), so index keys would let React reuse a removed item's
// instance — and its internal state — for the next same-kind item. Tools and
// gates carry callId; other kinds use seq. Streaming prose has seq 0 and falls
// back to index: it is only ever the single item at the tail of the list.
const itemKey = (item: StreamItem, i: number): string =>
  item.kind === 'tool' || item.kind === 'gate' ? `${item.kind}:${item.callId}`
  : item.seq > 0 ? `s:${item.seq}`
  : `i:${i}`

export default function ChatStream() {
  const activeId = useForge(st => st.activeId)
  const session = useForge(st => (st.activeId ? st.sessions[st.activeId] : undefined))
  const openDrawer = useForge(st => st.openDrawer)
  const scroller = useRef<HTMLDivElement>(null)

  const itemCount = session?.stream.items.length ?? 0
  // Streaming deltas grow the LAST item in place without changing the count,
  // so the follow-scroll also keys on the tail item's content size.
  const tail = session?.stream.items[itemCount - 1]
  const tailSize =
    tail?.kind === 'prose' ? tail.text.length
    : tail?.kind === 'tool' ? tail.output.length
    : 0
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [itemCount, activeId, tailSize])

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
          const key = itemKey(item, i)
          switch (item.kind) {
            case 'user':
              return <div key={key} className={s.userRow}><div className={s.userBubble}>{item.text}</div></div>
            case 'prose':
              return <div key={key} className={s.prose}><Markdown>{item.text}</Markdown></div>
            case 'tool':
              return <ToolCard key={key} item={item} onOpenPanel={idx => void openDrawer(idx)} />
            case 'gate':
              return (
                <ApprovalGate
                  key={key}
                  item={item}
                  onResolve={(decision, always) =>
                    void api.resolveApproval(session.id, item.callId, decision, always)}
                />
              )
            case 'error':
              return <div key={key} className={s.errorLine}>{item.message}</div>
            case 'info':
              return <div key={key} className={s.infoLine}>{item.text}</div>
            case 'compacted':
              return <div key={key} className={s.compacted}>· context compacted ·</div>
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
