import { useEffect, useState } from 'react'
import type { SubagentWorker } from '../state/reducer'
import { useForge } from '../state/store'
import s from './SubagentPanel.module.css'

const TICKER_LINES = 3

function Lane({ worker }: { worker: SubagentWorker }) {
  const [open, setOpen] = useState(false)
  const live = worker.state === 'queued' || worker.state === 'running'
    || worker.state === 'blocked'
  const hasReport = !live && worker.report !== ''
  const ticks = worker.activity.slice(-TICKER_LINES)
  const hiddenToolCalls = Math.max(0, worker.activityCount - ticks.length)

  return (
    <div className={s.lane}>
      <button
        className={s.laneHead}
        data-expandable={hasReport}
        onClick={() => hasReport && setOpen(o => !o)}
      >
        {live ? (
          <span className={s.dot} data-state={worker.state} aria-hidden="true" />
        ) : (
          <span className={s.glyph} data-state={worker.state} aria-hidden="true">
            {worker.state === 'done' ? '✓' : '✕'}
          </span>
        )}
        <span className={s.mode} data-mode={worker.mode}>{worker.mode}</span>
        <span className={s.task} title={worker.task}>{worker.task}</span>
        {hasReport && (
          <span className={s.laneChevron} aria-hidden="true">{open ? '▾' : '▸'}</span>
        )}
      </button>
      {worker.state === 'running' && (
        <div className={s.ticker}>
          {ticks.length === 0 ? (
            <div className={`${s.tick} ${s.waiting}`}>thinking…</div>
          ) : (
            <>
              {hiddenToolCalls > 0 && (
                <div className={s.previousCalls}>
                  +{hiddenToolCalls} previous tool {hiddenToolCalls === 1 ? 'call' : 'calls'}
                </div>
              )}
              {ticks.map((line, i) => (
                <div key={worker.activityCount - ticks.length + i}
                     className={s.tick} data-age={ticks.length - 1 - i}>
                  {line}
                </div>
              ))}
            </>
          )}
        </div>
      )}
      {worker.state === 'queued' && (
        <div className={s.ticker}>
          <div className={`${s.tick} ${s.waiting}`}>waiting for a slot…</div>
        </div>
      )}
      {worker.state === 'blocked' && (
        <div className={s.ticker}>
          <div className={`${s.tick} ${s.waiting}`}>waiting its turn…</div>
        </div>
      )}
      {open && hasReport && <pre className={s.report}>{worker.report}</pre>}
      {!live && !open && <div className={s.pad} />}
    </div>
  )
}

export default function SubagentPanel() {
  const crew = useForge(st =>
    st.activeId ? st.sessions[st.activeId].stream.subagents : null)
  const [expanded, setExpanded] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  // A fresh spawn always resurfaces the strip, even if the last crew was
  // dismissed; it starts collapsed so the feed leads.
  const callId = crew?.callId
  useEffect(() => {
    if (callId) { setExpanded(false); setDismissed(false) }
  }, [callId])

  if (!crew || crew.workers.length === 0 || dismissed) return null

  const running = crew.workers.filter(w => w.state === 'running').length
  // Blocked write workers hold a slot but wait on the write lock; group them
  // with slot-queued workers under the one "waiting" pill.
  const waiting = crew.workers.filter(
    w => w.state === 'queued' || w.state === 'blocked').length
  const done = crew.workers.filter(w => w.state === 'done').length
  const failed = crew.workers.filter(w => w.state === 'error').length
  const live = running + waiting

  const feed = crew.lastActivity
  const feedText =
    live === 0 ? (failed > 0 ? 'crew finished with failures' : 'crew finished')
    : feed ? feed.line
    : 'spinning up…'

  return (
    <div className={s.dock} data-expanded={expanded || undefined}>
      <button
        className={s.strip}
        aria-label="Subagents"
        aria-expanded={expanded}
        onClick={() => setExpanded(e => !e)}
      >
        <span className={s.pills}>
          {running > 0 && (
            <span className={s.pill} data-kind="running">
              <span className={s.pillDot} aria-hidden="true" />{running} running
            </span>
          )}
          {done > 0 && (
            <span className={s.pill} data-kind="done">
              <span className={s.pillDot} aria-hidden="true" />{done} done
            </span>
          )}
          {waiting > 0 && (
            <span className={s.pill} data-kind="waiting">
              <span className={s.pillDot} aria-hidden="true" />{waiting} waiting
            </span>
          )}
          {failed > 0 && (
            <span className={s.pill} data-kind="failed">
              <span className={s.pillDot} aria-hidden="true" />{failed} failed
            </span>
          )}
        </span>
        <span className={s.feed} data-idle={live === 0 || undefined}>
          {feed && live > 0 && (
            <span className={s.feedTag} aria-hidden="true">w{feed.worker}</span>
          )}
          {/* keyed so a new line retriggers the swap animation */}
          <span key={feedText} className={s.feedLine}>{feedText}</span>
        </span>
        <span className={s.chevron} aria-hidden="true">{expanded ? '▴' : '▾'}</span>
      </button>
      {live === 0 && (
        <button
          className={s.close}
          aria-label="Dismiss subagents panel"
          onClick={() => setDismissed(true)}
        >
          ✕
        </button>
      )}
      {expanded && (
        <div className={s.sheet} role="region" aria-label="Subagent details">
          {crew.workers.map(w => <Lane key={w.worker} worker={w} />)}
        </div>
      )}
    </div>
  )
}
