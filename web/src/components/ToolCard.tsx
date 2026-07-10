import { useState } from 'react'
import type { StreamItem } from '../state/reducer'
import s from './ToolCard.module.css'

const TAIL = 12

function fmtDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

export default function ToolCard({
  item,
  onOpenPanel,
}: {
  item: Extract<StreamItem, { kind: 'tool' }>
  onOpenPanel(changesetIndex: number): void
}) {
  const [collapsed, setCollapsed] = useState(true)
  const glyph = item.status === 'running' ? '▸' : item.status === 'done' ? '✓' : '!'
  const output = item.output === '(no output)' ? '' : item.output
  const lines = output ? output.replace(/\n$/, '').split('\n') : []
  const hidden = Math.max(0, lines.length - TAIL)
  const shown = lines.slice(-TAIL)
  const hasBody = shown.length > 0
  const bodyVisible = hasBody && !collapsed

  return (
    <div className={s.card}>
      <div
        className={s.header}
        data-body={bodyVisible}
        data-clickable={hasBody}
        onClick={() => hasBody && setCollapsed(c => !c)}
      >
        <span className={s.tile} data-status={item.status}>{glyph}</span>
        <span className={s.display}>{item.display}</span>
        {item.diffStats && (
          <span className={s.stats}>
            <span className={s.added}>+{item.diffStats.added}</span>
            <span className={s.removed}>−{item.diffStats.removed}</span>
          </span>
        )}
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
          {item.status !== 'running' && item.durationMs > 0 && (
            <span>{fmtDuration(item.durationMs)}</span>
          )}
          {hasBody && (
            <span className={s.chevron} aria-hidden="true">{collapsed ? '›' : '⌄'}</span>
          )}
        </span>
      </div>
      {bodyVisible && (
        <pre className={s.body}>
          {hidden > 0 && <div className={s.truncated}>… {hidden} earlier lines</div>}
          {shown.join('\n')}
        </pre>
      )}
    </div>
  )
}
