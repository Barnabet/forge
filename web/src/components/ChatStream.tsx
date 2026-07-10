import { useEffect, useRef } from 'react'
import Markdown from 'react-markdown'
import { api } from '../api'
import { useForge } from '../state/store'
import { familyOf, segmentItems } from '../lib/toolActivity'
import ApprovalGate from './ApprovalGate'
import ToolActivity from './ToolActivity'
import s from './ChatStream.module.css'

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
  // A new message from the user always snaps to the bottom, even if they had
  // scrolled up to read history.
  const lastUserSeq = session?.stream.items.findLast(it => it.kind === 'user')?.seq ?? 0
  const prevUserSeq = useRef(lastUserSeq)
  // Opening a conversation lands at the bottom. The flag stays armed until the
  // session has content: on boot activeId is set before events backfill.
  const needsBottom = useRef(true)
  useEffect(() => { needsBottom.current = true }, [activeId])
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const userSent = lastUserSeq > prevUserSeq.current
    prevUserSeq.current = lastUserSeq
    const opened = needsBottom.current
    if (opened && itemCount > 0) needsBottom.current = false
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (nearBottom || userSent || opened) el.scrollTop = el.scrollHeight
  }, [itemCount, activeId, tailSize, lastUserSeq])

  if (!session) return <div ref={scroller} className={s.scroller} />
  const { items, status, steps } = session.stream

  // "Thinking" only fills the silence (reasoning): hide it while text is
  // streaming in or a tool call is visibly running — the gutter spinner
  // attaches to those elements instead, marking where execution is.
  const hasLiveContent = items.some(it =>
    (it.kind === 'prose' && it.streaming) || (it.kind === 'tool' && it.status === 'running'))
  // Reasoning never follows text: once prose lands, the turn either moves to
  // a tool call or is over (the run may linger for post-turn bookkeeping like
  // memory updates), so "Thinking" after prose would be a lie.
  const afterText = items[items.length - 1]?.kind === 'prose'
  const statusText =
    status === 'running' ? (hasLiveContent || afterText ? null : 'Thinking')
    : status === 'attention' ? `Waiting on approval · step ${steps}`
    : status === 'queued' ? 'Queued — waiting for a slot'
    : null
  const thinking = statusText === 'Thinking'

  return (
    <div ref={scroller} className={s.scroller}>
      <div className={s.column}>
        {segmentItems(items).map(entry => {
          if (entry.kind === 'tools') {
            return (
              <div key={entry.key} className={s.activity}>
                {entry.groups.map(g => (
                  <ToolActivity
                    key={`${familyOf(g[0].tool)}:${g[0].callId}`}
                    items={g}
                    cwd={session.stream.cwd}
                    onOpenPanel={idx => void openDrawer(idx)}
                  />
                ))}
              </div>
            )
          }
          const { key, item } = entry
          switch (item.kind) {
            case 'user':
              return (
                <div key={key} className={s.userRow}>
                  <div className={s.userBubble}>
                    {item.images.length > 0 && (
                      <div className={s.userImages}>
                        {item.images.map((url, i) => <img key={i} src={url} alt="" />)}
                      </div>
                    )}
                    {item.text}
                  </div>
                </div>
              )
            case 'prose':
              // data-live puts the gutter spinner on the line being written
              return (
                <div key={key} className={s.prose} data-live={item.streaming || undefined}>
                  <Markdown>{item.text}</Markdown>
                </div>
              )
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
            default:
              return null
          }
        })}
        {status !== 'idle' && (
          // The slot is always mounted while a run is active so the status
          // flashing in/out never shifts the content above it.
          <div className={s.statusLine}>
            {statusText && (
              <>
                <span className={s.spinner} aria-hidden="true" />
                <span data-thinking={thinking || undefined}>{statusText}</span>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
