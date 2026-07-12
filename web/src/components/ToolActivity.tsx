import { useState } from 'react'
import { parseUnifiedDiff } from '../lib/diff'
import { familyOf, groupLabel, groupState, relDisplay, toolVerb, type ToolItem } from '../lib/toolActivity'
import { useForge } from '../state/store'
import { useLightbox } from './Lightbox'
import s from './ToolActivity.module.css'

const TAIL = 12

function fmtDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function DiffBody({ diff, reverted }: { diff: string; reverted: boolean }) {
  return (
    <div className={s.diff} data-reverted={reverted || undefined}>
      {parseUnifiedDiff(diff).map((hunk, hi) => (
        <div key={hi} className={s.hunk}>
          <div className={s.hunkHeader}>{hunk.header}</div>
          {hunk.lines.map((l, li) => (
            <div key={li} className={s.diffRow} data-kind={l.kind}>
              <span className={s.gutter}>
                {l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : (l.newNo ?? '')}
              </span>
              <span className={s.code}>{l.text}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function Line({
  item,
  cwd,
  indent,
}: {
  item: ToolItem
  cwd: string
  indent: boolean
}) {
  const diff = item.diffStats?.diff
  const hasDiff = !!diff
  // Tool bodies stay collapsed until the user explicitly opens them. This is
  // especially important for edit/write diffs, which can otherwise dominate
  // the chat stream as soon as the call finishes.
  const [open, setOpen] = useState(false)
  const [reverted, setReverted] = useState(false)
  const revert = useForge(st => st.revert)
  const openImage = useLightbox(st => st.open)
  const running = item.status === 'running'
  const inFlight = running || !!item.pending
  const output = item.output === '(no output)' ? '' : item.output
  const lines = output ? output.replace(/\n$/, '').split('\n') : []
  const hidden = Math.max(0, lines.length - TAIL)
  const shown = lines.slice(-TAIL)
  const images = item.images ?? []
  const hasBody = hasDiff || shown.length > 0 || images.length > 0
  // A completed successful tool with no output is explicitly marked so it is
  // distinguishable from a still-pending/running call, which renders nothing.
  const noOutput = item.status === 'done' && !hasBody

  return (
    <div data-indent={indent || undefined} className={s.line}>
      <div
        className={s.row}
        data-clickable={hasBody}
        onClick={() => hasBody && setOpen(o => !o)}
      >
        {inFlight && <span className={s.pulse} aria-hidden="true" />}
        <span className={s.verb}>{toolVerb(item)}</span>
        {/* pending: arguments haven't landed, so there's nothing to name yet —
            the "About to X" verb stands alone. Every later state has a display. */}
        {!item.pending && (
          <span className={s.object}>{relDisplay(item.display, cwd)}</span>
        )}
        {item.diffStats && (
          <span className={s.stats}>
            <span className={s.added}>+{item.diffStats.added}</span>
            <span className={s.removed}>−{item.diffStats.removed}</span>
          </span>
        )}
        {item.status === 'error' && <span className={s.failed}>failed</span>}
        <span className={s.meta}>
          {hasDiff && (
            <button
              className={s.revert}
              disabled={reverted}
              onClick={async e => {
                e.stopPropagation() // revert must not toggle the collapse
                await revert(item.diffStats!.changeset_index)
                setReverted(true)
              }}
            >
              {reverted ? 'reverted' : 'Revert'}
            </button>
          )}
          {item.autoApproved && <span>auto-approved</span>}
          {noOutput && <span className={s.noOutput}>no output</span>}
          {!running && item.durationMs > 0 && <span>{fmtDuration(item.durationMs)}</span>}
          {hasBody && (
            <span className={s.chevron} aria-hidden="true">{open ? '⌄' : '›'}</span>
          )}
        </span>
      </div>
      {open && hasDiff && <DiffBody diff={diff} reverted={reverted} />}
      {open && !hasDiff && shown.length > 0 && (
        <pre className={s.body}>
          {hidden > 0 && <div className={s.truncated}>… {hidden} earlier lines</div>}
          {shown.join('\n')}
        </pre>
      )}
      {open && images.length > 0 && (
        <div className={s.images}>
          {images.map((url, i) => (
            <img key={i} src={url} alt={`page ${i + 1}`} onClick={() => openImage(url)} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function ToolActivity({
  items,
  cwd = '',
}: {
  items: ToolItem[]
  cwd?: string
}) {
  const [open, setOpen] = useState(false)
  if (items.length === 1)
    return <Line item={items[0]} cwd={cwd} indent={false} />

  const inFlight = items.some(i => i.status === 'running')
  const failed = items.filter(i => i.status === 'error').length
  // Edit groups are per-file (see segmentItems): the header names the file
  // and sums the diff stats across the individual edits.
  const isEdit = familyOf(items[0].tool) === 'edit'
  const allWrites = isEdit && items.every(i => i.tool === 'write_file')
  const editVerb = allWrites
    ? { about: 'About to write', gerund: 'Writing', past: 'Wrote' }
    : { about: 'About to edit', gerund: 'Editing', past: 'Edited' }
  const added = items.reduce((n, i) => n + (i.diffStats?.added ?? 0), 0)
  const removed = items.reduce((n, i) => n + (i.diffStats?.removed ?? 0), 0)
  return (
    <div>
      <div className={s.row} data-clickable="true" onClick={() => setOpen(o => !o)}>
        {inFlight && <span className={s.pulse} aria-hidden="true" />}
        {isEdit ? (
          <>
            <span className={s.verb}>{editVerb[groupState(items)]}</span>
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
        <Line key={it.callId} item={it} cwd={cwd} indent />
      ))}
    </div>
  )
}
