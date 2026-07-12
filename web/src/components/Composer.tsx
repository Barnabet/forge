import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Autonomy, Effort } from '../protocol'
import { useForge } from '../state/store'
import CommandPalette from './CommandPalette'
import ContextMeter from './ContextMeter'
import FilePicker from './FilePicker'
import PillSelect from './PillSelect'
import s from './Composer.module.css'

const EFFORT_OPTIONS: { value: Effort; label: string }[] = [
  { value: 'default', label: 'default' },
  { value: 'low', label: 'low' },
  { value: 'medium', label: 'medium' },
  { value: 'high', label: 'high' },
]

const AUTONOMY_OPTIONS: { value: Autonomy; label: string; hint: string }[] = [
  { value: 'yolo', label: 'yolo', hint: 'auto-approve' },
  { value: 'guarded', label: 'guarded', hint: 'ask first' },
]

export function paletteQuery(draft: string): string | null {
  const m = /^\/(\S*)$/.exec(draft)
  return m ? m[1] : null
}

export function atQuery(draft: string): string | null {
  const m = /(?:^|\s)@([\w./-]*)$/.exec(draft)
  return m ? m[1] : null
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = () => reject(r.error as Error)
    r.readAsDataURL(file)
  })
}

const DRAFT_KEY = 'forge.composer.draft'

export default function Composer() {
  // Restored from localStorage so an app crash/reload never eats a half-written prompt.
  const [draft, setDraft] = useState(() => localStorage.getItem(DRAFT_KEY) ?? '')
  const [images, setImages] = useState<string[]>([])
  const [dragOver, setDragOver] = useState(false)
  const boxRef = useRef<HTMLTextAreaElement>(null)
  const send = useForge(st => st.send)
  const models = useForge(st => st.models)
  const healthy = useForge(st => st.healthy)
  const activeId = useForge(st => st.activeId)
  const stream = useForge(st => (st.activeId ? st.sessions[st.activeId].stream : undefined))

  const modelName =
    models.find(m => m.id === stream?.model)?.display_name ?? stream?.model ?? ''

  const archived = stream?.archived ?? false
  const planMode = stream?.mode === 'plan'
  const running = stream?.status !== undefined && stream.status !== 'idle'

  const usage = stream?.usageTokens ?? 0
  const ctxWindow = models.find(m => m.id === stream?.model)?.context_window ?? 0
  const ctxPct = usage > 0 && ctxWindow > 0
    ? Math.min(100, Math.round((usage / ctxWindow) * 100))
    : null

  const palette = archived ? null : paletteQuery(draft)
  const at = archived || palette !== null ? null : atQuery(draft)

  const submit = () => {
    const text = draft.trim()
    if ((!text && images.length === 0) || palette !== null) return
    setDraft('')
    setImages([])
    void send(text, images)
  }

  const addFiles = async (files: Iterable<File>) => {
    const imgs = [...files].filter(f => f.type.startsWith('image/'))
    if (imgs.length === 0) return
    const urls = await Promise.all(imgs.map(readAsDataUrl))
    setImages(prev => [...prev, ...urls])
  }

  useEffect(() => {
    if (draft) localStorage.setItem(DRAFT_KEY, draft)
    else localStorage.removeItem(DRAFT_KEY)
  }, [draft])

  useLayoutEffect(() => {
    const el = boxRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`
    el.style.overflowY = el.scrollHeight > 140 ? 'auto' : 'hidden'
  }, [draft])

  return (
    <div className={s.wrap}>
      <div
        className={s.card}
        data-dragover={dragOver || undefined}
        data-plan={planMode || undefined}
        onDragOver={e => {
          if (archived || ![...e.dataTransfer.items].some(i => i.kind === 'file')) return
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={e => {
          if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOver(false)
        }}
        onDrop={e => {
          if (archived) return
          e.preventDefault()
          setDragOver(false)
          void addFiles(e.dataTransfer.files)
        }}
      >
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
        {images.length > 0 && (
          <div className={s.attachments}>
            {images.map((url, i) => (
              <div key={i} className={s.thumb}>
                <img src={url} alt={`attachment ${i + 1}`} />
                <button
                  className={s.thumbRemove}
                  aria-label={`Remove attachment ${i + 1}`}
                  onClick={() => setImages(prev => prev.filter((_, j) => j !== i))}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={boxRef}
          className={s.input}
          rows={1}
          disabled={archived}
          placeholder={archived ? 'Archived — unarchive to continue'
            : 'Reply, steer, or queue another task…'}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onPaste={e => {
            const files = [...e.clipboardData.files]
            if (files.some(f => f.type.startsWith('image/'))) {
              e.preventDefault()
              void addFiles(files)
            }
          }}
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
          {ctxPct !== null && activeId && (
            <ContextMeter
              usage={usage}
              window={ctxWindow}
              pct={ctxPct}
              disabled={archived}
              onCompact={() => void api.compact(activeId)}
            />
          )}
          <span
            className={s.modelPill}
            title={healthy ? undefined : 'CLIProxyAPI unreachable'}
          >
            {!healthy && <span className={s.healthDot} />}
            {planMode && <span className={s.planTag}>plan · </span>}
            {activeId && stream ? (
              <PillSelect
                disabled={archived}
                segments={[
                  {
                    key: 'model',
                    value: stream.model,
                    options: models.map(m => ({ value: m.id, label: m.display_name })),
                    onPick: m => void api.setModel(activeId, m),
                  },
                  {
                    key: 'effort',
                    value: stream.effort ?? 'default',
                    options: EFFORT_OPTIONS,
                    onPick: e => void api.setEffort(activeId, e as Effort),
                  },
                  {
                    key: 'autonomy',
                    value: stream.autonomy ?? 'yolo',
                    options: AUTONOMY_OPTIONS,
                    onPick: a => void api.setAutonomy(activeId, a as Autonomy),
                  },
                ]}
              />
            ) : (
              modelName
            )}
          </span>
          {running && (
            <button
              className={s.stop}
              aria-label="Stop"
              title="Stop the current run"
              disabled={!activeId}
              onClick={() => activeId && void api.cancel(activeId)}
            >
              ■
            </button>
          )}
          <button
            className={s.send}
            aria-label="Send"
            disabled={archived || (!draft.trim() && images.length === 0)}
            onClick={submit}
          >
            ↑
          </button>
        </div>
      </div>
    </div>
  )
}
