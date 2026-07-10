import { useRef, useState } from 'react'
import { useForge } from '../state/store'
import CommandPalette from './CommandPalette'
import FilePicker from './FilePicker'
import s from './Composer.module.css'

export function paletteQuery(draft: string): string | null {
  const m = /^\/(\S*)$/.exec(draft)
  return m ? m[1] : null
}

export function atQuery(draft: string): string | null {
  const m = /(?:^|\s)@([\w./-]*)$/.exec(draft)
  return m ? m[1] : null
}

export default function Composer() {
  const [draft, setDraft] = useState('')
  const boxRef = useRef<HTMLTextAreaElement>(null)
  const send = useForge(st => st.send)
  const models = useForge(st => st.models)
  const healthy = useForge(st => st.healthy)
  const stream = useForge(st => (st.activeId ? st.sessions[st.activeId].stream : undefined))

  const modelName =
    models.find(m => m.id === stream?.model)?.display_name ?? stream?.model ?? ''

  const palette = paletteQuery(draft)
  const at = palette === null ? atQuery(draft) : null

  const submit = () => {
    const text = draft.trim()
    if (!text || palette !== null) return
    setDraft('')
    void send(text)
  }

  const autosize = () => {
    const el = boxRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`
  }

  return (
    <div className={s.wrap}>
      <div className={s.card}>
        {palette !== null && (
          <CommandPalette query={palette} onClose={() => setDraft('')} />
        )}
        {at !== null && (
          <FilePicker
            query={at}
            onPick={path => {
              setDraft(d => d.replace(/@[\w./-]*$/, `${path} `))
              boxRef.current?.focus()
            }}
          />
        )}
        <textarea
          ref={boxRef}
          className={s.input}
          rows={1}
          placeholder="Reply, steer, or queue another task…"
          value={draft}
          onChange={e => { setDraft(e.target.value); autosize() }}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />
        <div className={s.footer}>
          <span className={s.chip}>@ files</span>
          <span className={s.chip}>/ commands</span>
          <span className={s.spacer} />
          <span
            className={s.modelPill}
            title={healthy ? undefined : 'CLIProxyAPI unreachable'}
          >
            {!healthy && <span className={s.healthDot} />}
            {modelName} · {stream?.autonomy ?? 'yolo'}
          </span>
          <button
            className={s.send}
            aria-label="Send"
            disabled={!draft.trim()}
            onClick={submit}
          >
            ↑
          </button>
        </div>
      </div>
    </div>
  )
}
