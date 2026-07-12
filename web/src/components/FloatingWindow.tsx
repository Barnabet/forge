import { useRef, useState, type PointerEvent, type ReactNode } from 'react'
import s from './FloatingWindow.module.css'

const MIN_W = 280
const MIN_H = 200

export default function FloatingWindow({
  title,
  ariaLabel,
  focused,
  onClose,
  onFocus,
  zIndex,
  initialX,
  initialY,
  children,
}: {
  title: ReactNode
  ariaLabel: string
  focused: boolean
  onClose(): void
  onFocus(): void
  zIndex: number
  initialX: number
  initialY: number
  children: ReactNode
}) {
  const [pos, setPos] = useState({ x: initialX, y: initialY })
  const [size, setSize] = useState({ w: 640, h: 480 })
  const drag = useRef<{ dx: number; dy: number } | null>(null)
  const resize = useRef<{ x: number; y: number; w: number; h: number } | null>(null)

  const onDragDown = (e: PointerEvent) => {
    // Ignore the close button so its click still fires.
    if ((e.target as HTMLElement).closest('button')) return
    drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onDragMove = (e: PointerEvent) => {
    if (!drag.current) return
    const x = Math.min(Math.max(e.clientX - drag.current.dx, -size.w + 80), window.innerWidth - 40)
    const y = Math.min(Math.max(e.clientY - drag.current.dy, 0), window.innerHeight - 40)
    setPos({ x, y })
  }
  const onDragUp = (e: PointerEvent) => {
    if (!drag.current) return
    drag.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId))
      e.currentTarget.releasePointerCapture(e.pointerId)
  }

  const onResizeDown = (e: PointerEvent) => {
    e.stopPropagation()
    resize.current = { x: e.clientX, y: e.clientY, w: size.w, h: size.h }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onResizeMove = (e: PointerEvent) => {
    if (!resize.current) return
    const w = Math.max(MIN_W, resize.current.w + (e.clientX - resize.current.x))
    const h = Math.max(MIN_H, resize.current.h + (e.clientY - resize.current.y))
    setSize({ w, h })
  }
  const onResizeUp = (e: PointerEvent) => {
    if (!resize.current) return
    resize.current = null
    if (e.currentTarget.hasPointerCapture(e.pointerId))
      e.currentTarget.releasePointerCapture(e.pointerId)
  }

  return (
    <div
      className={s.window}
      data-focused={focused || undefined}
      role="dialog"
      aria-label={ariaLabel}
      style={{ left: pos.x, top: pos.y, width: size.w, height: size.h, zIndex }}
      onMouseDown={onFocus}
    >
      <div
        className={s.titleBar}
        onPointerDown={onDragDown}
        onPointerMove={onDragMove}
        onPointerUp={onDragUp}
      >
        <span className={s.title}>{title}</span>
        <button className={s.close} aria-label="Close" onClick={onClose}>✕</button>
      </div>
      <div className={s.body}>{children}</div>
      <div
        className={s.resize}
        aria-label="Resize"
        onPointerDown={onResizeDown}
        onPointerMove={onResizeMove}
        onPointerUp={onResizeUp}
      />
    </div>
  )
}
