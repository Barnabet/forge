import s from './Sparks.module.css'

// The "Forge is working" indicator: sparks flying off a struck anvil. On each
// cycle every ember fires at once — a single strike — fanning outward along its
// own vector (--dx/--dy), then a beat of stillness before the next strike, so
// it reads as rhythmic hammering rather than a continuous drizzle.
// Sizing/positioning come from the caller's className (the old spinner slots).
const SPARKS = [
  { dx: -5.5, dy: -3.5 },
  { dx: -2.8, dy: -7.5 },
  { dx: 0.2, dy: -8.5 },
  { dx: 3.2, dy: -7 },
  { dx: 5.8, dy: -3 },
  { dx: -1.5, dy: -5.5 },
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
              }}
            />
          )
        })}
      </g>
    </svg>
  )
}
