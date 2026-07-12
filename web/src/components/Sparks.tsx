import s from './Sparks.module.css'

// The "Forge is working" indicator: sparks spraying off a struck anvil. On
// each cycle every ember fires at once — a single strike — but in a tight
// directional cone up-and-to-the-left (not a radial starburst, which reads as
// fireworks), then a beat of stillness before the next strike. The travel
// vectors cluster around one diagonal; a gravity droop pulls the tail back
// down as each streak decays, so it reads as a real spark spray.
// Sizing/positioning come from the caller's className (the old spinner slots).
const SPARKS = [
  { dx: -3.5, dy: -8.5 },
  { dx: -5.5, dy: -7 },
  { dx: -7, dy: -5 },
  { dx: -6.2, dy: -3 },
  { dx: -4.6, dy: -6 },
  { dx: -2.4, dy: -7.2 },
]

export function Sparks({ className }: { className?: string }) {
  return (
    <svg
      className={className ? `${s.sparks} ${className}` : s.sparks}
      // Framed tightly around the animated spark spread (bbox center ≈ 8.5,12.9
      // in the 0–24 draw space) so box-centering in the gutter lands it right.
      viewBox="2 6.5 13 13"
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
