import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import s from './PillSelect.module.css'

export type PillOption = { value: string; label: string; hint?: string }

export type PillSegment = {
  key: string
  value: string
  options: PillOption[]
  onPick: (v: string) => void
}

// The three model-pill segments share ONE menu. Hovering a segment slides that
// single menu under it and swaps its content, so it reads as one selector that
// moves — never two popovers cross-fading over each other.
export default function PillSelect({
  segments,
  disabled,
}: {
  segments: PillSegment[]
  disabled?: boolean
}) {
  const [active, setActive] = useState<number | null>(null)
  const [left, setLeft] = useState(0)
  const rootRef = useRef<HTMLSpanElement>(null)
  const triggerRefs = useRef<(HTMLButtonElement | null)[]>([])
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const cancelClose = () => {
    if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null }
  }
  // Brief grace so crossing the trigger→menu gap doesn't close the menu.
  const scheduleClose = () => {
    cancelClose()
    closeTimer.current = setTimeout(() => setActive(null), 120)
  }

  // Center the shared menu under whichever trigger is active.
  useLayoutEffect(() => {
    if (active === null) return
    const trigger = triggerRefs.current[active]
    const root = rootRef.current
    if (!trigger || !root) return
    setLeft(trigger.offsetLeft + trigger.offsetWidth / 2)
  }, [active])

  useEffect(() => {
    if (active === null) return
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setActive(null)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setActive(null) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [active])

  useEffect(() => cancelClose, [])

  const seg = active === null ? null : segments[active]

  return (
    <span
      className={s.group}
      ref={rootRef}
      onMouseLeave={scheduleClose}
    >
      {segments.map((sg, i) => (
        <span key={sg.key} className={s.seg}>
          {i > 0 && <span className={s.dot}>·</span>}
          <button
            type="button"
            ref={el => { triggerRefs.current[i] = el }}
            className={s.trigger}
            disabled={disabled}
            data-open={active === i || undefined}
            aria-haspopup="listbox"
            aria-expanded={active === i}
            onMouseEnter={() => { if (!disabled) { cancelClose(); setActive(i) } }}
            onClick={() => setActive(a => (a === i ? null : i))}
          >
            {sg.options.find(o => o.value === sg.value)?.label ?? sg.value}
          </button>
        </span>
      ))}
      {seg && (
        <div
          className={s.menu}
          style={{ left }}
          role="listbox"
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
        >
          {seg.options.map(o => (
            <button
              key={o.value}
              type="button"
              className={s.opt}
              role="option"
              aria-selected={o.value === seg.value}
              data-active={o.value === seg.value || undefined}
              onClick={() => { seg.onPick(o.value); setActive(null) }}
            >
              <span className={s.check}>{o.value === seg.value ? '✓' : ''}</span>
              <span className={s.optLabel}>{o.label}</span>
              {o.hint && <span className={s.optHint}>{o.hint}</span>}
            </button>
          ))}
        </div>
      )}
    </span>
  )
}
