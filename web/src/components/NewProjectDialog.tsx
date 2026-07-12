import { useState } from 'react'
import { useForge } from '../state/store'
import FolderBrowser from './FolderBrowser'
import Modal from './Modal'
import s from './Dialogs.module.css'

export default function NewProjectDialog() {
  const models = useForge(st => st.models)
  const closeDialog = useForge(st => st.closeDialog)
  const createProject = useForge(st => st.createProject)
  const [name, setName] = useState('')
  const [cwd, setCwd] = useState('')
  const [model, setModel] = useState('')
  const [autonomy, setAutonomy] = useState('')
  const [effort, setEffort] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (!name.trim() || !cwd.trim()) return
    try {
      await createProject({
        name: name.trim(),
        cwd: cwd.trim(),
        ...(model && { default_model: model }),
        ...(autonomy && { default_autonomy: autonomy }),
        ...(effort && { default_effort: effort }),
      })
      closeDialog()
    } catch {
      setError('Not a valid folder path')
    }
  }

  return (
    <Modal title="New project" onClose={closeDialog}>
      <input className={s.nameInput} placeholder="Project name" value={name}
             onChange={e => setName(e.target.value)} autoFocus />
      <FolderBrowser value={cwd} onChange={setCwd} />
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
        <button className={s.accent} disabled={!name.trim() || !cwd.trim()}
                onClick={() => void submit()}>
          Create project
        </button>
      </div>
    </Modal>
  )
}
