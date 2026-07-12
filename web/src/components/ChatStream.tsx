import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { useForge } from '../state/store'
import { familyOf, segmentItems } from '../lib/toolActivity'
import ApprovalGate from './ApprovalGate'
import ConfirmDialog from './ConfirmDialog'
import { useLightbox } from './Lightbox'
import PlanCard from './PlanCard'
import ToolActivity from './ToolActivity'
import TodoStrip from './TodoStrip'
import { Sparks } from './Sparks'
import s from './ChatStream.module.css'

// Module-level so the array identity is stable across renders.
const gfmPlugins = [remarkGfm]

// Total summary sections the compaction pass walks through (mirrors the
// server's COMPACT_SECTIONS). Drives the determinate progress bar.
const COMPACTION_TOTAL = 9

function CompactionProgress({ phase, label }: { phase: number; label: string }) {
  const pct = Math.round((Math.min(phase, COMPACTION_TOTAL) / COMPACTION_TOTAL) * 100)
  return (
    <div className={s.compaction}>
      <div className={s.compactionHead}>
        <span>{phase === 0 ? 'Compacting context' : `Compacting context · ${label}`}</span>
        <span className={s.compactionCount}>{phase}/{COMPACTION_TOTAL}</span>
      </div>
      <div className={s.compactionTrack}>
        <div
          className={s.compactionFill}
          data-indeterminate={phase === 0 || undefined}
          style={phase === 0 ? undefined : { width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

const elapsedSince = (since: number) => Math.floor(Math.max(0, Date.now() - since) / 1000)

function ThinkingTimer({ since }: { since: number }) {
  const [elapsed, setElapsed] = useState(() => elapsedSince(since))

  useEffect(() => {
    // Recompute immediately so switching to a session with an older anchor
    // shows its real elapsed value instead of stale local state.
    setElapsed(elapsedSince(since))
    const interval = window.setInterval(() => {
      setElapsed(elapsedSince(since))
    }, 1000)

    return () => window.clearInterval(interval)
  }, [since])

  return <span data-thinking aria-label={`${elapsed} seconds elapsed`}>{elapsed}s</span>
}

export default function ChatStream() {
  const activeId = useForge(st => st.activeId)
  const session = useForge(st => (st.activeId ? st.sessions[st.activeId] : undefined))
  const submitEdit = useForge(st => st.submitEdit)
  const rewind = useForge(st => st.rewind)
  const openImage = useLightbox(st => st.open)
  const [confirmTarget, setConfirmTarget] = useState<{ sid: string; seq: number } | null>(null)
  // The user message being edited inline, its live draft, and — once the user
  // hits Save — the payload awaiting the workspace-restore confirmation.
  const [editing, setEditing] = useState<{ seq: number; text: string; images: string[] } | null>(null)
  const [pendingEdit, setPendingEdit] = useState<{ seq: number; text: string; images: string[] } | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  const rawStream = session?.stream.items ?? []
  const itemCount = rawStream.length
  // Streaming deltas grow an item in place without changing the count, so the
  // follow-scroll also keys on the growing item's content size. It's usually
  // the tail, but a parked pending steering bubble can sit below it.
  const growing = rawStream.findLast(
    it => (it.kind === 'prose' && it.streaming) || (it.kind === 'tool' && it.status === 'running'))
  const tailSize =
    growing?.kind === 'prose' ? growing.text.length
    : growing?.kind === 'tool' ? growing.output.length
    : 0
  // A new message from the user always snaps to the bottom, even if they had
  // scrolled up to read history.
  const lastUserSeq = session?.stream.items.findLast(it => it.kind === 'user')?.seq ?? 0
  const prevUserSeq = useRef(lastUserSeq)
  // Opening a conversation lands at the bottom. The flag stays armed until the
  // session has content: on boot activeId is set before events backfill.
  const needsBottom = useRef(true)
  // Whether the user is parked at the bottom, captured from scroll events so it
  // reflects the position *before* new content grows the scroller. Measuring in
  // the effect instead would read the post-insert height: a single big block
  // pushes the gap past the threshold and follow-scroll would wrongly abort.
  const atBottom = useRef(true)
  const onScroll = () => {
    const el = scroller.current
    if (el) atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }
  useEffect(() => {
    needsBottom.current = true
    atBottom.current = true
    setConfirmTarget(null)
    setEditing(null)
    setPendingEdit(null)
  }, [activeId])
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const userSent = lastUserSeq > prevUserSeq.current
    prevUserSeq.current = lastUserSeq
    const opened = needsBottom.current
    if (opened && itemCount > 0) needsBottom.current = false
    if (atBottom.current || userSent || opened) {
      el.scrollTop = el.scrollHeight
      atBottom.current = true
    }
  }, [itemCount, activeId, tailSize, lastUserSeq])

  // A ghosted steering message lives at its send position in the log, but the
  // agent keeps producing output (prose, tool calls) before it consumes the
  // message. Render pending bubbles pinned to the bottom so that later output
  // appears above them and the bubble doesn't jump up the transcript when the
  // reducer relocates it on un-ghost.
  const hasPending = rawStream.some(it => it.kind === 'user' && it.pending)
  const items = hasPending
    ? [...rawStream.filter(it => !(it.kind === 'user' && it.pending)),
       ...rawStream.filter(it => it.kind === 'user' && it.pending)]
    : rawStream
  const status = session?.stream.status ?? 'idle'
  const steps = session?.stream.steps ?? 0

  // A rewind (local or remote) that removes the message being edited strands an
  // invalid target; drop the inline editor so it can't submit against a gone seq.
  const editSeq = editing?.seq
  const editSeqGone =
    editSeq !== undefined && !items.some(it => it.kind === 'user' && it.seq === editSeq)
  useEffect(() => {
    if (editSeqGone) {
      setEditing(null)
      setPendingEdit(null)
    }
  }, [editSeqGone])

  // "Thinking" only fills the silence (reasoning): hide it while text is
  // streaming in or a tool call is visibly running — the gutter spinner
  // attaches to those elements instead, marking where execution is.
  const hasLiveContent = items.some(it =>
    (it.kind === 'prose' && it.streaming) || (it.kind === 'tool' && it.status === 'running'))
  // Reasoning never follows text: once prose lands, the turn either moves to
  // a tool call or is over (the run may linger for post-turn bookkeeping like
  // memory updates), so "Thinking" after prose would be a lie.
  const afterText = items[items.length - 1]?.kind === 'prose'
  // Compaction can run at idle (manual /compact) or mid-run (auto): surface it
  // above every run status so the user always sees when it starts and ends.
  const compacting = session?.stream.compacting ?? false
  const compactionPhase = session?.stream.compactionPhase ?? 0
  const compactionLabel = session?.stream.compactionLabel ?? ''
  const statusText =
    compacting ? null // rendered as a dedicated progress bar below
    : status === 'running' ? (hasLiveContent || afterText ? null : 'Thinking')
    : status === 'attention' ? `Waiting on approval · step ${steps}`
    : status === 'queued' ? 'Queued — waiting for a slot'
    : null
  const thinkingSince = session?.stream.thinkingSince ?? null
  const thinking = statusText === 'Thinking'

  if (!session) return <div ref={scroller} className={s.scroller} onScroll={onScroll} />

  return (
    <div ref={scroller} className={s.scroller} onScroll={onScroll}>
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
                  />
                ))}
              </div>
            )
          }
          const { key, item } = entry
          switch (item.kind) {
            case 'user': {
              const hasCheckpoint = !!item.checkpoint
              // Pending steering bubbles aren't stable targets yet.
              const actionable = hasCheckpoint && !item.pending
              if (editing?.seq === item.seq) {
                return (
                  <div key={key} className={s.userRow}>
                    <div className={s.editBubble}>
                      {item.images.length > 0 && (
                        <div className={s.userImages}>
                          {item.images.map((url, i) => (
                          <img key={i} src={url} alt="" onClick={() => openImage(url)} />
                        ))}
                        </div>
                      )}
                      <textarea
                        className={s.editInput}
                        autoFocus
                        rows={Math.min(10, editing.text.split('\n').length)}
                        value={editing.text}
                        onChange={e => setEditing({ ...editing, text: e.target.value })}
                        onKeyDown={e => {
                          if (e.key === 'Escape') {
                            e.preventDefault()
                            setEditing(null)
                          } else if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault()
                            if (editing.text.trim()) setPendingEdit(editing)
                          }
                        }}
                      />
                      <div className={s.editActions}>
                        <button className={s.editCancel} onClick={() => setEditing(null)}>
                          Cancel
                        </button>
                        <button
                          className={s.editSave}
                          disabled={!editing.text.trim()}
                          onClick={() => setPendingEdit(editing)}
                        >
                          Save
                        </button>
                      </div>
                    </div>
                  </div>
                )
              }
              return (
                <div key={key} className={s.userRow}>
                  <div className={s.userBubble} data-pending={item.pending || undefined}>
                    {item.images.length > 0 && (
                      <div className={s.userImages}>
                        {item.images.map((url, i) => (
                          <img key={i} src={url} alt="" onClick={() => openImage(url)} />
                        ))}
                      </div>
                    )}
                    {item.text}
                  </div>
                  {actionable ? (
                    <div className={s.userActions}>
                      <button
                        className={s.userAction}
                        onClick={() => setEditing({ seq: item.seq, text: item.text, images: item.images })}
                        aria-label="Edit from here"
                        title="Edit this message and resend from here"
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M12 20h9" />
                          <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                        </svg>
                      </button>
                      <button
                        className={s.userAction}
                        onClick={() => setConfirmTarget({ sid: session.id, seq: item.seq })}
                        aria-label="Rewind here"
                        title="Rewind the conversation and workspace to this message"
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
                          <path d="M3 3v5h5" />
                        </svg>
                      </button>
                    </div>
                  ) : !item.pending && (
                    <div className={s.userActions}>
                      <span
                        className={s.userActionDisabled}
                        title="No code checkpoint was captured for this message."
                        aria-disabled="true"
                      >
                        No checkpoint
                      </span>
                    </div>
                  )}
                </div>
              )
            }
            case 'prose':
              // data-live puts the gutter sparks on the line being written
              return (
                <div key={key} className={s.prose} data-live={item.streaming || undefined}>
                  {item.streaming && <Sparks className={s.proseSparks} />}
                  <Markdown remarkPlugins={gfmPlugins}>{item.text}</Markdown>
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
            case 'plan':
              return (
                <PlanCard
                  key={key}
                  item={item}
                  onResolve={(decision, feedback) =>
                    void api.resolvePlan(session.id, item.callId, decision, feedback)}
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
        {(status !== 'idle' || compacting) && (
          // The slot is always mounted while a run is active so the status
          // flashing in/out never shifts the content above it. Manual
          // compaction runs at idle, so include it in the mount condition.
          <div className={s.statusLine}>
            {compacting ? (
              <CompactionProgress phase={compactionPhase} label={compactionLabel} />
            ) : statusText && (
              <>
                <Sparks className={s.spinner} />
                <span data-thinking={thinking || undefined}>{statusText}</span>
                {thinking && thinkingSince !== null && <ThinkingTimer since={thinkingSince} />}
              </>
            )}
          </div>
        )}
      </div>
      <div className={s.todoDock}>
        <div className={s.todoDockInner}><TodoStrip /></div>
      </div>
      {confirmTarget !== null && (
        <ConfirmDialog
          title="Rewind here?"
          body="This permanently discards every message after this one and restores the workspace to this point. This can't be undone."
          confirmLabel="Rewind"
          onConfirm={() => {
            const { sid, seq } = confirmTarget
            setConfirmTarget(null)
            if (sid === activeId) void rewind(seq)
          }}
          onCancel={() => setConfirmTarget(null)}
        />
      )}
      {pendingEdit !== null && (
        <ConfirmDialog
          title="Save edit?"
          body="Saving replaces every message after this one and restores the workspace to this point. This can't be undone."
          confirmLabel="Save & rewind"
          onConfirm={() => {
            const { seq, text, images } = pendingEdit
            setPendingEdit(null)
            setEditing(null)
            void submitEdit(seq, text.trim(), images)
          }}
          onCancel={() => setPendingEdit(null)}
        />
      )}
    </div>
  )
}
