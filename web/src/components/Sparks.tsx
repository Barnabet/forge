import s from './Sparks.module.css'

// The "Forge is working" indicator: sparks flying off a struck anvil. Small
// ember streaks eject from a low strike point in a fan, arc outward and fade —
// a repeating shower, not a static flame. Each streak carries its own travel
// vector (--dx/--dy) and staggered delay so the burst never looks in lockstep.
// Sizing/positioning come from the caller's className (the old spinner slots).
const SPARKS = [
  { dx: -5.5, dy: -3.5, delay: '0s', dur: '0.85s' },
  { dx: -2.8, dy: -7.5, delay: '-0.15s', dur: '0.95s' },
  { dx: 0.2, dy: -8.5, delay: '-0.5s', dur: '0.9s' },
  { dx: 3.2, dy: -7, delay: '-0.28s', dur: '1s' },
  { dx: 5.8, dy: -3, delay: '-0.62s', dur: '0.88s' },
  { dx: -1.5, dy: -5.5, delay: '-0.78s', dur: '0.8s' },
]

export function Sparks({ className }: { className?: string }) {
  return (
    <svg
      className={className ? `${s.sparks} ${className}` : s.sparks}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <g fill="var(--spark)">
        {SPARKS.map((sp, i) => {
          // point the streak along its travel vector (rect is drawn pointing up)
          const rot = (Math.atan2(sp.dy, sp.dx) * 180) / Math.PI + 90
          return (
            <rect
              key={i}
              className={s.spark}
              x={11.4}
              y={14}
              width={1.2}
              height={3.2}
              rx={0.6}
              style={{
                ['--dx' as string]: sp.dx,
                ['--dy' as string]: sp.dy,
                ['--rot' as string]: `${rot}deg`,
                animationDelay: sp.delay,
                animationDuration: sp.dur,
              }}
            />
          )
        })}
      </g>
    </svg>
  )
}
