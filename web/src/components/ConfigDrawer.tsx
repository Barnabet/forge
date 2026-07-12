import { useEffect, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { api, ApiError } from '../api'
import { ICON_THEMES, loadIconProvider, useIconProvider, type IconMode } from '../lib/icons'
import { resolveUiTheme, UI_THEMES } from '../lib/theme'
import { useForge } from '../state/store'
import type { ConfigPatch, ForgeConfig } from '../protocol'
import SubagentLeaderboard from './SubagentLeaderboard'
import s from './ConfigDrawer.module.css'

// Sample rows rendered as a live preview of each icon theme.
const PREVIEW = ['main.ts', 'app.py', 'README.md']

function ThemePreview({ theme, mode }: {
  theme: (typeof ICON_THEMES)[number]['id']
  mode: IconMode
}) {
  const icons = useIconProvider(theme)
  if (!icons) return <span className={s.preview} />
  return (
    <span className={s.preview}>
      {PREVIEW.map(n => (
        <img key={n} className={s.previewIcon} data-theme={theme} src={icons.file(n, mode)} alt="" aria-hidden />
      ))}
    </span>
  )
}

// Scalar fields we diff and patch. `models` is never sent.
const SCALAR_KEYS = [
  'base_url', 'api_key', 'default_model', 'default_autonomy', 'max_concurrent',
  'max_resident_sessions', 'serper_api_key', 'firecrawl_api_key', 'openrouter_api_key',
  'embedding_model', 'image_model', 'memory_similarity_threshold', 'max_subagents',
  'subagent_max_turns', 'subagent_model', 'memory_model', 'compaction_model',
] as const satisfies readonly (keyof ConfigPatch)[]

type Form = Omit<ForgeConfig, 'models'>

function toForm(c: ForgeConfig): Form {
  const { models: _models, ...rest } = c
  return rest
}

function diff(base: Form, cur: Form): ConfigPatch {
  const patch: ConfigPatch = {}
  for (const k of SCALAR_KEYS) {
    if (base[k] !== cur[k]) (patch as Record<string, unknown>)[k] = cur[k]
  }
  return patch
}

function Row({ label, keyName, children }: {
  label: string
  keyName: string
  children: React.ReactNode
}) {
  return (
    <label className={s.row}>
      <span className={s.rowLabel}>{label}</span>
      {children}
      <span className={s.rowKey}>{keyName}</span>
    </label>
  )
}

export default function ConfigDrawer({ onClose, anchorRef }: {
  onClose: () => void
  anchorRef?: RefObject<HTMLButtonElement | null>
}) {
  const iconTheme = useForge(st => st.iconTheme)
  const setIconTheme = useForge(st => st.setIconTheme)
  const uiTheme = useForge(st => st.uiTheme)
  const setUiTheme = useForge(st => st.setUiTheme)
  const models = useForge(st => st.models)
  const iconMode = resolveUiTheme(uiTheme)

  const panelRef = useRef<HTMLDivElement>(null)

  const [leaderboardOpen, setLeaderboardOpen] = useState(false)
  const [baseline, setBaseline] = useState<Form | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [reveal, setReveal] = useState(false)

  const dirty = baseline && form ? Object.keys(diff(baseline, form)).length > 0 : false

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // The leaderboard owns Escape while it is open; don't close settings too.
      if (e.key === 'Escape' && !leaderboardOpen) onClose()
    }
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (leaderboardOpen) return
      if (panelRef.current?.contains(t)) return
      if (anchorRef?.current?.contains(t)) return
      onClose()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [onClose, anchorRef, leaderboardOpen])

  // Warm the icon themes' chunks so previews and switching feel instant.
  useEffect(() => {
    for (const t of ICON_THEMES) void loadIconProvider(t.id).catch(() => undefined)
  }, [])

  // Fetch server config once on mount.
  useEffect(() => {
    let live = true
    api.getConfig()
      .then(c => { if (live) { setBaseline(toForm(c)); setForm(toForm(c)) } })
      .catch(() => { if (live) setLoadError(true) })
    return () => { live = false }
  }, [])

  const set = <K extends keyof Form>(k: K, v: Form[K]) =>
    setForm(f => (f ? { ...f, [k]: v } : f))

  const num = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value === '' ? 0 : Number(e.target.value)
    set(k, v as unknown as Form[typeof k])
  }
  const txt = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    set(k, e.target.value as unknown as Form[typeof k])

  const save = async () => {
    if (!baseline || !form) return
    const patch = diff(baseline, form)
    if (Object.keys(patch).length === 0) return
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await api.updateConfig(patch)
      setBaseline(toForm(updated))
      setForm(toForm(updated))
    } catch (e) {
      setSaveError(
        e instanceof ApiError && e.status === 400
          ? 'Could not save — check the highlighted values.'
          : 'Could not save — please try again.',
      )
    } finally {
      setSaving(false)
    }
  }

  const secret = (label: string, key: keyof Form) => (
    <Row label={label} keyName={key}>
      <input
        className={s.input}
        type={reveal ? 'text' : 'password'}
        autoComplete="off"
        value={(form?.[key] as string) ?? ''}
        onChange={txt(key)}
      />
    </Row>
  )

  return createPortal(
    <>
      <div className={s.overlay} onMouseDown={onClose} />
      <div ref={panelRef} className={s.drawer} role="dialog" aria-label="Settings">
        <header className={s.header}>
          <div className={s.headerTitle}>Settings</div>
          <button className={s.close} aria-label="Close settings" onClick={onClose}>✕</button>
        </header>

        <div className={s.scroll}>
          <section className={s.section}>
            <div className={s.sectionTitle}>APPEARANCE</div>

            <div className={s.label}>UI THEME</div>
            <div className={s.segRow} role="radiogroup" aria-label="UI theme">
              {UI_THEMES.map(t => (
                <button
                  key={t.id}
                  className={t.id === uiTheme ? s.segActive : s.seg}
                  role="radio"
                  aria-checked={t.id === uiTheme}
                  onClick={() => setUiTheme(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            <div className={s.label}>FILE ICONS</div>
            {ICON_THEMES.map(t => (
              <button
                key={t.id}
                className={t.id === iconTheme ? s.optionActive : s.option}
                role="radio"
                aria-checked={t.id === iconTheme}
                onClick={() => setIconTheme(t.id)}
              >
                <span className={s.check}>{t.id === iconTheme ? '✓' : ''}</span>
                <span className={s.optionName}>{t.label}</span>
                <ThemePreview theme={t.id} mode={iconMode} />
              </button>
            ))}
          </section>

          <section className={s.section}>
            <div className={s.sectionTitle}>CONFIGURATION</div>

            {loadError && <div className={s.error}>Could not load configuration.</div>}
            {!loadError && !form && <div className={s.loading}>Loading…</div>}

            {form && (
              <>
                <div className={s.group}>
                  <div className={s.groupTitle}>Defaults</div>
                  <Row label="Default model" keyName="default_model">
                    <select
                      className={s.input}
                      aria-label="Default model"
                      value={form.default_model}
                      onChange={e => set('default_model', e.target.value)}
                    >
                      <option value="">(none)</option>
                      {models.map(m => (
                        <option key={m.id} value={m.id}>{m.display_name}</option>
                      ))}
                    </select>
                  </Row>
                  <Row label="Autonomy" keyName="default_autonomy">
                    <span className={s.segRow} role="radiogroup" aria-label="Default autonomy">
                      {(['yolo', 'guarded'] as const).map(a => (
                        <button
                          key={a}
                          type="button"
                          className={a === form.default_autonomy ? s.segActive : s.seg}
                          role="radio"
                          aria-checked={a === form.default_autonomy}
                          onClick={() => set('default_autonomy', a)}
                        >
                          {a}
                        </button>
                      ))}
                    </span>
                  </Row>
                </div>

                <div className={s.group}>
                  <div className={s.groupTitle}>Concurrency</div>
                  <Row label="Max concurrent" keyName="max_concurrent">
                    <input className={s.input} type="number" min={1}
                           value={form.max_concurrent} onChange={num('max_concurrent')} />
                  </Row>
                  <Row label="Max resident sessions" keyName="max_resident_sessions">
                    <input className={s.input} type="number" min={1}
                           value={form.max_resident_sessions} onChange={num('max_resident_sessions')} />
                  </Row>
                </div>

                <div className={s.group}>
                  <div className={s.groupTitle}>Subagents</div>
                  <Row label="Max subagents" keyName="max_subagents">
                    <input className={s.input} type="number" min={0}
                           value={form.max_subagents} onChange={num('max_subagents')} />
                  </Row>
                  <Row label="Max turns" keyName="subagent_max_turns">
                    <input className={s.input} type="number" min={0}
                           value={form.subagent_max_turns} onChange={num('subagent_max_turns')} />
                  </Row>
                  <Row label="Subagent model" keyName="subagent_model">
                    <input className={s.input} type="text" placeholder="inherit session model"
                           value={form.subagent_model} onChange={txt('subagent_model')} />
                  </Row>
                  <Row label="Compaction model" keyName="compaction_model">
                    <input className={s.input} type="text" placeholder="inherit session model"
                           value={form.compaction_model} onChange={txt('compaction_model')} />
                  </Row>
                  <button type="button" className={s.ghost}
                          onClick={() => setLeaderboardOpen(true)}>
                    Subagent leaderboard
                  </button>
                </div>

                <div className={s.group}>
                  <div className={s.groupTitle}>Memory</div>
                  <Row label="Similarity threshold" keyName="memory_similarity_threshold">
                    <input className={s.input} type="number" step={0.05} min={0} max={1}
                           value={form.memory_similarity_threshold}
                           onChange={num('memory_similarity_threshold')} />
                  </Row>
                  <Row label="Memory model" keyName="memory_model">
                    <input className={s.input} type="text" placeholder="inherit session model"
                           value={form.memory_model} onChange={txt('memory_model')} />
                  </Row>
                  <Row label="Embedding model" keyName="embedding_model">
                    <input className={s.input} type="text"
                           value={form.embedding_model} onChange={txt('embedding_model')} />
                  </Row>
                </div>

                <div className={s.group}>
                  <div className={s.groupTitle}>Images</div>
                  <Row label="Image model" keyName="image_model">
                    <input className={s.input} type="text"
                           value={form.image_model} onChange={txt('image_model')} />
                  </Row>
                </div>

                <div className={s.group}>
                  <div className={s.groupHead}>
                    <div className={s.groupTitle}>Connection &amp; keys</div>
                    <button type="button" className={s.reveal}
                            aria-pressed={reveal}
                            onClick={() => setReveal(r => !r)}>
                      {reveal ? 'Hide' : 'Reveal'}
                    </button>
                  </div>
                  <Row label="Base URL" keyName="base_url">
                    <input className={s.input} type="text"
                           value={form.base_url} onChange={txt('base_url')} />
                  </Row>
                  {secret('API key', 'api_key')}
                  {secret('Serper API key', 'serper_api_key')}
                  {secret('Firecrawl API key', 'firecrawl_api_key')}
                  {secret('OpenRouter API key', 'openrouter_api_key')}
                </div>
              </>
            )}
          </section>
        </div>

        <footer className={s.footer}>
          {saveError && <div className={s.error}>{saveError}</div>}
          <div className={s.footnote}>Changes apply immediately; in-flight runs finish on their current settings.</div>
          <div className={s.actions}>
            <button className={s.ghost} onClick={onClose}>Done</button>
            <button className={s.accent} disabled={!dirty || saving}
                    onClick={() => void save()}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </footer>
      </div>
      {leaderboardOpen && <SubagentLeaderboard onClose={() => setLeaderboardOpen(false)} />}
    </>,
    document.body,
  )
}
