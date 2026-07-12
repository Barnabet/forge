import { useEffect } from 'react'
import { create } from 'zustand'
import s from './Lightbox.module.css'

interface LightboxState {
  url: string | null
  open(url: string): void
  close(): void
}

export const useLightbox = create<LightboxState>(set => ({
  url: null,
  open: url => set({ url }),
  close: () => set({ url: null }),
}))

export default function Lightbox() {
  const url = useLightbox(st => st.url)
  const close = useLightbox(st => st.close)

  useEffect(() => {
    if (!url) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [url, close])

  if (!url) return null
  return (
    <div className={s.overlay} onMouseDown={close}>
      <img className={s.image} src={url} alt="" onMouseDown={e => e.stopPropagation()} />
    </div>
  )
}
