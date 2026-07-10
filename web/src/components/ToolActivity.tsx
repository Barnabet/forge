import { useState } from 'react'
import { familyOf, groupLabel, relDisplay, toolVerb, type ToolItem } from '../lib/toolActivity'
import s from './ToolActivity.module.css'

const TAIL = 12

function fmtDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function Line({
  item,
  cwd,
  indent,
  onOpenPanel,
}: {
  item: ToolItem
  cwd: string
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
        <span className={s.object}>{item.display ? relDisplay(item.display, cwd) : '…'}</span>
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
  cwd = '',
  onOpenPanel,
}: {
  items: ToolItem[]
  cwd?: string
  onOpenPanel(changesetIndex: number): void
}) {
  const [open, setOpen] = useState(false)
  if (items.length === 1)
    return <Line item={items[0]} cwd={cwd} indent={false} onOpenPanel={onOpenPanel} />

  const running = items.some(i => i.status === 'running')
  const failed = items.filter(i => i.status === 'error').length
  // Edit groups are per-file (see segmentItems): the header names the file
  // and sums the diff stats across the individual edits.
  const isEdit = familyOf(items[0].tool) === 'edit'
  const allWrites = isEdit && items.every(i => i.tool === 'write_file')
  const added = items.reduce((n, i) => n + (i.diffStats?.added ?? 0), 0)
  const removed = items.reduce((n, i) => n + (i.diffStats?.removed ?? 0), 0)
  return (
    <div>
      <div className={s.row} data-clickable="true" onClick={() => setOpen(o => !o)}>
        {running && <span className={s.pulse} aria-hidden="true" />}
        {isEdit ? (
          <>
            <span className={s.verb}>
              {running ? (allWrites ? 'Writing' : 'Editing') : allWrites ? 'Wrote' : 'Edited'}
            </span>
            <span className={s.object}>{relDisplay(items[0].display, cwd)}</span>
            <span className={s.count}>× {items.length}</span>
            {added + removed > 0 && (
              <span className={s.stats}>
                <span className={s.added}>+{added}</span>
                <span className={s.removed}>−{removed}</span>
              </span>
            )}
          </>
        ) : (
          <span className={s.verb}>{groupLabel(items)}</span>
        )}
        {failed > 0 && <span className={s.failed}>{failed} failed</span>}
        <span className={s.meta}>
          <span className={s.chevron} aria-hidden="true">{open ? '⌄' : '›'}</span>
        </span>
      </div>
      {open && items.map(it => (
        <Line key={it.callId} item={it} cwd={cwd} indent onOpenPanel={onOpenPanel} />
      ))}
    </div>
  )
}
