import { useState } from 'react'
import { groupLabel, toolVerb, type ToolItem } from '../lib/toolActivity'
import s from './ToolActivity.module.css'

const TAIL = 12

function fmtDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function Line({
  item,
  indent,
  onOpenPanel,
}: {
  item: ToolItem
  indent: boolean
  onOpenPanel(changesetIndex: number): void
}) {
  const [open, setOpen] = useState(false)
  const running = item.status === 'running'
  const output = item.output === '(no output)' ? '' : item.output
  const lines = output ? output.replace(/\n$/, '').split('\n') : []
  const hidden = Math.max(0, lines.length - TAIL)
  const shown = lines.slice(-TAIL)
  const hasBody = shown.length > 0

  return (
    <div data-indent={indent || undefined} className={s.line}>
      <div
        className={s.row}
        data-clickable={hasBody}
        onClick={() => hasBody && setOpen(o => !o)}
      >
        {running && <span className={s.pulse} aria-hidden="true" />}
        <span className={s.verb}>{toolVerb(item)}</span>
        <span className={s.object}>{item.display}</span>
        {item.diffStats && (
          <span className={s.stats}>
            <span className={s.added}>+{item.diffStats.added}</span>
            <span className={s.removed}>−{item.diffStats.removed}</span>
          </span>
        )}
        {item.status === 'error' && <span className={s.failed}>failed</span>}
        <span className={s.meta}>
          {item.diffStats && (
            <button
              className={s.openPanel}
              onClick={e => {
                e.stopPropagation() // panel link must not toggle the collapse
                onOpenPanel(item.diffStats!.changeset_index)
              }}
            >
              open panel →
            </button>
          )}
          {item.autoApproved && <span>auto-approved</span>}
          {!running && item.durationMs > 0 && <span>{fmtDuration(item.durationMs)}</span>}
          {hasBody && (
            <span className={s.chevron} aria-hidden="true">{open ? '⌄' : '›'}</span>
          )}
        </span>
      </div>
      {open && hasBody && (
        <pre className={s.body}>
          {hidden > 0 && <div className={s.truncated}>… {hidden} earlier lines</div>}
          {shown.join('\n')}
        </pre>
      )}
    </div>
  )
}

export default function ToolActivity({
  items,
  onOpenPanel,
}: {
  items: ToolItem[]
  onOpenPanel(changesetIndex: number): void
}) {
  const [open, setOpen] = useState(false)
  if (items.length === 1) return <Line item={items[0]} indent={false} onOpenPanel={onOpenPanel} />

  const running = items.some(i => i.status === 'running')
  const failed = items.filter(i => i.status === 'error').length
  return (
    <div>
      <div className={s.row} data-clickable="true" onClick={() => setOpen(o => !o)}>
        {running && <span className={s.pulse} aria-hidden="true" />}
        <span className={s.verb}>{groupLabel(items)}</span>
        {failed > 0 && <span className={s.failed}>{failed} failed</span>}
        <span className={s.meta}>
          <span className={s.chevron} aria-hidden="true">{open ? '⌄' : '›'}</span>
        </span>
      </div>
      {open && items.map(it => (
        <Line key={it.callId} item={it} indent onOpenPanel={onOpenPanel} />
      ))}
    </div>
  )
}
