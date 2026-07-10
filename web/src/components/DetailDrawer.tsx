import { parseUnifiedDiff } from '../lib/diff'
import { useForge } from '../state/store'
import s from './DetailDrawer.module.css'

export default function DetailDrawer() {
  const session = useForge(st => (st.activeId ? st.sessions[st.activeId] : undefined))
  const setDrawerView = useForge(st => st.setDrawerView)
  const closeDrawer = useForge(st => st.closeDrawer)
  const stepDrawer = useForge(st => st.stepDrawer)
  const revert = useForge(st => st.revert)
  const keepAll = useForge(st => st.keepAll)

  if (!session?.drawer.open) return null
  const { changesets, drawer, fileContent } = session
  const cs = changesets[drawer.changesetIndex]

  let dir = ''
  let base = ''
  if (cs) {
    const rel = cs.path.startsWith(session.stream.cwd)
      ? cs.path.slice(session.stream.cwd.length).replace(/^\//, '')
      : cs.path
    const slash = rel.lastIndexOf('/')
    dir = slash >= 0 ? rel.slice(0, slash + 1) : ''
    base = rel.slice(slash + 1)
  }

  return (
    <aside className={s.drawer}>
      <header className={s.header}>
        {cs && (
          <>
            <span className={s.dir}>{dir}</span>
            <span className={s.file}>{base}</span>
            <span className={s.chipAdd}>+{cs.added}</span>
            <span className={s.chipDel}>−{cs.removed}</span>
            {cs.status !== 'pending' && <span className={s.chipStatus}>{cs.status}</span>}
          </>
        )}
        <div className={s.seg}>
          {(['diff', 'file', 'blame'] as const).map(v => (
            <button key={v}
                    className={drawer.view === v ? s.segActive : s.segBtn}
                    onClick={() => void setDrawerView(v)}>
              {v[0].toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>
        <button className={s.close} aria-label="Close" onClick={closeDrawer}>✕</button>
      </header>

      <div className={s.body}>
        {cs && drawer.view === 'diff' &&
          parseUnifiedDiff(cs.diff).map((hunk, hi) => (
            <div key={hi} className={s.hunk}>
              <div className={s.hunkHeader}>{hunk.header}</div>
              {hunk.lines.map((l, li) => (
                <div key={li} className={s.row} data-kind={l.kind}>
                  <span className={s.gutter}>
                    {l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : (l.newNo ?? '')}
                  </span>
                  <span className={s.code}>{l.text}</span>
                </div>
              ))}
            </div>
          ))}
        {drawer.view === 'file' && (
          fileContent === null
            ? <div className={s.stub}>Loading…</div>
            : <pre className={s.fileView}>{fileContent}</pre>
        )}
        {drawer.view === 'blame' && <div className={s.stub}>Blame — post-V1</div>}
      </div>

      <footer className={s.footer}>
        <button className={s.pager} aria-label="‹" onClick={() => void stepDrawer(-1)}>‹</button>
        <button className={s.pager} aria-label="›" onClick={() => void stepDrawer(1)}>›</button>
        <span className={s.count}>
          {drawer.changesetIndex + 1} of {changesets.length} files changed
        </span>
        <button className={s.ghost} onClick={() => void revert()}>Revert</button>
        <button className={s.keep} onClick={() => void keepAll()}>Keep all</button>
      </footer>
    </aside>
  )
}
