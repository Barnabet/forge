import s from './Sparks.module.css'

/**
 * The "Forge agent working" indicator. A brief hammer-impact flash launches
 * ember streaks from one point; they spread, arc down, and disappear before
 * the next strike. Sizing and gutter position come from the caller.
 */
export function Sparks({ className }: { className?: string }) {
  return (
    <span
      className={[s.sparks, className].filter(Boolean).join(' ')}
      aria-hidden="true"
    >
      <svg viewBox="0 0 24 24" className={s.svg}>
        <circle className={s.impact} cx="12" cy="18.5" r="1.4" />

        <line className={`${s.spark} ${s.farLeft}`} x1="10.8" y1="17.7" x2="7.4" y2="16.2" />
        <line className={`${s.spark} ${s.highLeft}`} x1="11.2" y1="17.2" x2="9.3" y2="13.8" />
        <line className={`${s.spark} ${s.top}`} x1="12" y1="16.9" x2="12.3" y2="12.8" />
        <line className={`${s.spark} ${s.highRight}`} x1="12.8" y1="17.2" x2="15" y2="14" />
        <line className={`${s.spark} ${s.farRight}`} x1="13.2" y1="17.7" x2="16.8" y2="16" />
        <circle className={`${s.ember} ${s.lowRight}`} cx="14" cy="18" r="0.65" />
      </svg>
    </span>
  )
}
