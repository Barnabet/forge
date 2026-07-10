import { useState } from 'react'
import type { StreamItem } from '../state/reducer'
import s from './ApprovalGate.module.css'

export default function ApprovalGate({
  item,
  onResolve,
}: {
  item: Extract<StreamItem, { kind: 'gate' }>
  onResolve(decision: 'allow' | 'deny', always?: { pattern: string; scope: 'session' | 'global' }): void
}) {
  const [menuOpen, setMenuOpen] = useState(false)

  if (item.denied) {
    return (
      <div className={s.deniedRow}>
        <span className={s.deniedGlyph}>✕</span>
        <span>Denied</span>
        <span className={s.deniedCmd}>{item.display}</span>
      </div>
    )
  }

  const alwaysOptions = [
    { label: 'Always allow this command (session)', pattern: item.display, scope: 'session' as const },
    { label: `Always allow ${item.tool} (session)`, pattern: '*', scope: 'session' as const },
    { label: `Always allow ${item.tool} (global)`, pattern: '*', scope: 'global' as const },
  ]

  return (
    <div className={s.gate}>
      <span className={s.tile}>⚠</span>
      <div className={s.textCol}>
        <div className={s.title}>Approval required</div>
        <div className={s.command}>{item.display}</div>
      </div>
      <div className={s.actions}>
        <button className={s.allow} onClick={() => onResolve('allow', undefined)}>Allow</button>
        <button className={s.ghost} onClick={() => onResolve('deny', undefined)}>Deny</button>
        <span className={s.alwaysWrap}>
          <button className={s.ghost} onClick={() => setMenuOpen(o => !o)}>Always ⌄</button>
          {menuOpen && (
            <div className={s.menu}>
              {alwaysOptions.map(o => (
                <button
                  key={o.label}
                  className={s.menuItem}
                  onClick={() => {
                    setMenuOpen(false)
                    onResolve('allow', { pattern: o.pattern, scope: o.scope })
                  }}
                >
                  {o.label}
                </button>
              ))}
            </div>
          )}
        </span>
      </div>
    </div>
  )
}
