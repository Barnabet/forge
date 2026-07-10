import { useEffect, useState } from 'react'
import { api } from '../api'
import { useForge } from '../state/store'
import s from './Popover.module.css'

export default function FilePicker({
  query,
  onPick,
}: {
  query: string
  onPick(path: string): void
}) {
  const activeId = useForge(st => st.activeId)
  const [results, setResults] = useState<string[]>([])

  useEffect(() => {
    if (!activeId) return
    let live = true
    const t = setTimeout(() => {
      api.searchFiles(activeId, query)
        .then(r => { if (live) setResults(r.slice(0, 8)) })
        .catch(() => { if (live) setResults([]) })
    }, 120)
    return () => { live = false; clearTimeout(t) }
  }, [activeId, query])

  return (
    <div className={s.popover}>
      {results.length === 0 && <div className={s.empty}>no matches</div>}
      {results.map(path => (
        <button key={path} className={`${s.row} ${s.rowMono}`} onClick={() => onPick(path)}>
          {path}
        </button>
      ))}
    </div>
  )
}
