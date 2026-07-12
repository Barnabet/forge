import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import { useForge } from '../state/store'
import FolderBrowser from './FolderBrowser'
import Modal from './Modal'
import s from './Dialogs.module.css'

export default function NewSessionDialog() {
  const models = useForge(st => st.models)
  const closeDialog = useForge(st => st.closeDialog)
  const newAdhocSession = useForge(st => st.newAdhocSession)
  const [cwd, setCwd] = useState('')
  const [model, setModel] = useState('')
  const [autonomy, setAutonomy] = useState('')
  const [effort, setEffort] = useState('')
  const [recents, setRecents] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.recentDirs().then(setRecents).catch(() => setRecents([]))
  }, [])

  const submit = async () => {
    if (!cwd.trim()) return
    try {
      await newAdhocSession({
        cwd: cwd.trim(),
        ...(model && { model }),
        ...(autonomy && { autonomy }),
        ...(effort && { effort }),
      })
      closeDialog()
    } catch (e) {
      setError(e instanceof ApiError && e.status === 400
        ? 'Not a valid folder path' : 'Could not create the session')
    }
  }

  return (
    <Modal title="New session" onClose={closeDialog}>
      <FolderBrowser value={cwd} onChange={setCwd} />
      {recents.length > 0 && (
        <div className={s.recents}>
          {recents.map(r => (
            <button key={r} className={s.recentRow} onClick={() => setCwd(r)}>{r}</button>
          ))}
        </div>
      )}
      <div className={s.selects}>
        <label className={s.field}>
          Model
          <select aria-label="Model" value={model} onChange={e => setModel(e.target.value)}>
            <option value="">(config default)</option>
            {models.map(m => <option key={m.id} value={m.id}>{m.display_name}</option>)}
          </select>
        </label>
        <label className={s.field}>
          Autonomy
          <select aria-label="Autonomy" value={autonomy} onChange={e => setAutonomy(e.target.value)}>
            <option value="">(default)</option>
            <option value="yolo">yolo</option>
            <option value="guarded">guarded</option>
          </select>
        </label>
        <label className={s.field}>
          Effort
          <select aria-label="Effort" value={effort} onChange={e => setEffort(e.target.value)}>
            <option value="">(default)</option>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>
        </label>
      </div>
      {error && <div className={s.error}>{error}</div>}
      <div className={s.actions}>
        <button className={s.ghost} onClick={closeDialog}>Cancel</button>
        <button className={s.accent} disabled={!cwd.trim()} onClick={() => void submit()}>
          Start session
        </button>
      </div>
    </Modal>
  )
}
