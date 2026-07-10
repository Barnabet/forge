import { useState } from 'react'
import { api, ApiError } from '../api'
import { useForge } from '../state/store'
import s from './Popover.module.css'

const COMMANDS = [
  { cmd: 'new', hint: 'start a new session' },
  { cmd: 'model', hint: 'switch model' },
  { cmd: 'autonomy', hint: 'yolo / guarded' },
  { cmd: 'effort', hint: 'reasoning effort' },
  { cmd: 'compact', hint: 'compact context now' },
  { cmd: 'cancel', hint: 'stop the current run' },
]

export default function CommandPalette({
  query,
  onClose,
}: {
  query: string
  onClose(): void
}) {
  const [step, setStep] = useState<'root' | 'model' | 'autonomy' | 'effort'>('root')
  const [error, setError] = useState<string | null>(null)
  const activeId = useForge(st => st.activeId)
  const models = useForge(st => st.models)
  const openDialog = useForge(st => st.openDialog)

  const run = async (fn: () => Promise<void>) => {
    try {
      await fn()
      onClose()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409)
        setError('Session is running — try after the run finishes')
      else setError('Command failed')
    }
  }

  const pick = (cmd: string) => {
    if (!activeId && cmd !== 'new') return
    switch (cmd) {
      case 'new':
        openDialog('new-session')
        onClose()
        return
      case 'model': return setStep('model')
      case 'autonomy': return setStep('autonomy')
      case 'effort': return setStep('effort')
      case 'compact': return void run(() => api.compact(activeId!))
      case 'cancel': return void run(() => api.cancel(activeId!))
    }
  }

  return (
    <div className={s.popover}>
      {error && <div className={s.error}>{error}</div>}
      {step === 'root' &&
        COMMANDS.filter(c => c.cmd.startsWith(query)).map(c => (
          <button key={c.cmd} className={s.row} onClick={() => pick(c.cmd)}>
            <span className={s.rowMono}>/{c.cmd}</span>
            <span className={s.hint}>{c.hint}</span>
          </button>
        ))}
      {step === 'model' &&
        models.map(m => (
          <button key={m.id} className={s.row}
                  onClick={() => void run(() => api.setModel(activeId!, m.id))}>
            {m.display_name}
            <span className={s.hint}>{m.id}</span>
          </button>
        ))}
      {step === 'autonomy' &&
        (['yolo', 'guarded'] as const).map(a => (
          <button key={a} className={s.row}
                  onClick={() => void run(() => api.setAutonomy(activeId!, a))}>
            {a}
          </button>
        ))}
      {step === 'effort' &&
        (['default', 'low', 'medium', 'high'] as const).map(lvl => (
          <button key={lvl} className={s.row}
                  onClick={() => void run(() => api.setEffort(activeId!, lvl))}>
            {lvl}
          </button>
        ))}
    </div>
  )
}
