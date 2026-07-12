import { useEffect, useRef, useState } from 'react'
import s from './ContextMeter.module.css'

function fmtTokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)
}

// The context-usage pill with a hover flyout: a clean used/max readout, a fill
// bar, and a Compact button (same effect as the /compact command).
export default function ContextMeter({
  usage,
  window: ctxWindow,
  pct,
  onCompact,
  disabled,
}: {
  usage: number
  window: number
  pct: number
  onCompact: () => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const cancelClose = () => {
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null }
  }
  const scheduleClose = () => {
    cancelClose()
    closeTimer.current = setTimeout(() => setOpen(false), 120)
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  useEffect(() => cancelClose, [])

  return (
    <span
      className={s.wrap}
      ref={ref}
      onMouseEnter={cancelClose}
      onMouseLeave={scheduleClose}
    >
      <button
        type="button"
        className={s.pill}
        data-warn={pct >= 75}
        aria-haspopup="dialog"
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onClick={() => setOpen(o => !o)}
      >
        {fmtTokens(usage)} · {pct}%
      </button>
      {open && (
        <div className={s.menu} role="dialog" aria-label="Context usage">
          <div className={s.head}>
            <span className={s.label}>Context</span>
            <span className={s.pct} data-warn={pct >= 75}>{pct}%</span>
          </div>
          <div className={s.bar}>
            <span
              className={s.fill}
              data-warn={pct >= 75}
              style={{ width: `${Math.max(2, pct)}%` }}
            />
          </div>
          <div className={s.readout}>
            <span>{usage.toLocaleString()}</span>
            <span className={s.slash}>/</span>
            <span>{ctxWindow.toLocaleString()}</span>
            <span className={s.unit}>tokens</span>
          </div>
          <button
            type="button"
            className={s.compact}
            disabled={disabled}
            onClick={() => { onCompact(); setOpen(false) }}
          >
            Compact conversation
          </button>
        </div>
      )}
    </span>
  )
}
