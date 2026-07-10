import { useEffect, type ReactNode } from 'react'
import s from './Modal.module.css'

export default function Modal({
  title,
  onClose,
  children,
}: {
  title: string
  onClose(): void
  children: ReactNode
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className={s.overlay} onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className={s.card} role="dialog" aria-label={title}>
        <div className={s.title}>{title}</div>
        {children}
      </div>
    </div>
  )
}
