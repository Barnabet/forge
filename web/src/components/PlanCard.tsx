import { useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { StreamItem } from '../state/reducer'
import s from './PlanCard.module.css'

// Module-level so the array identity is stable across renders.
const gfmPlugins = [remarkGfm]

const BADGE: Record<string, string> = {
  pending: 'awaiting approval',
  approved: 'approved',
  revising: 'changes requested',
}

export default function PlanCard({
  item,
  onResolve,
}: {
  item: Extract<StreamItem, { kind: 'plan' }>
  onResolve(decision: 'approve' | 'revise', feedback?: string): void
}) {
  const pending = item.state === 'pending'
  const [expanded, setExpanded] = useState(pending)
  const [revising, setRevising] = useState(false)
  const [feedback, setFeedback] = useState('')

  const sendFeedback = () => {
    const text = feedback.trim()
    if (!text) return
    setRevising(false)
    setFeedback('')
    onResolve('revise', text)
  }

  return (
    <div className={s.card} data-state={item.state}>
      <button
        className={s.header}
        onClick={() => { if (!pending) setExpanded(e => !e) }}
        disabled={pending}
      >
        <span className={s.label}>PLAN</span>
        <span className={s.badge} data-state={item.state}>{BADGE[item.state]}</span>
        {!pending && <span className={s.chevron}>{expanded ? '▾' : '▸'}</span>}
      </button>
      {expanded && (
        <div className={s.body}>
          <Markdown remarkPlugins={gfmPlugins}>{item.plan}</Markdown>
        </div>
      )}
      {item.state === 'revising' && item.feedback && (
        <div className={s.feedbackNote}>Requested: {item.feedback}</div>
      )}
      {pending && (
        <div className={s.actions}>
          {!revising ? (
            <>
              <button className={s.approve} onClick={() => onResolve('approve')}>
                Approve &amp; execute
              </button>
              <button className={s.ghost} onClick={() => setRevising(true)}>
                Request changes
              </button>
            </>
          ) : (
            <div className={s.reviseRow}>
              <textarea
                className={s.reviseInput}
                placeholder="What should change?"
                value={feedback}
                autoFocus
                onChange={e => setFeedback(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    sendFeedback()
                  }
                  if (e.key === 'Escape') setRevising(false)
                }}
              />
              <button className={s.ghost} onClick={sendFeedback}>Send</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
