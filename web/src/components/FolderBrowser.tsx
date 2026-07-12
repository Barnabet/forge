import { useState } from 'react'
import { api } from '../api'
import d from './Dialogs.module.css'
import s from './FolderBrowser.module.css'

export default function FolderBrowser({
  value, onChange,
}: { value: string; onChange: (path: string) => void }) {
  const [picking, setPicking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const pick = async () => {
    setPicking(true)
    setError(null)
    try {
      const r = await api.fsPick(value || undefined)
      if (r.path) onChange(r.path)
    } catch {
      setError('Native folder picker is unavailable here — type a path instead.')
    } finally {
      setPicking(false)
    }
  }

  return (
    <div className={s.wrap}>
      <input
        className={d.pathInput}
        placeholder="/path/to/folder"
        value={value}
        onChange={e => onChange(e.target.value)}
      />
      <button
        type="button"
        className={s.browse}
        onClick={() => void pick()}
        disabled={picking}
      >
        {picking ? 'Opening…' : 'Browse…'}
      </button>
      {error && <div className={s.error}>{error}</div>}
    </div>
  )
}
