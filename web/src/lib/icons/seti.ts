// Seti — minimal single-color glyphs (VS Code's built-in Seti theme).
// seti-icons returns { svg, color } with named colors; we bake the official
// Seti UI palette in and serve data: URLs. The set has no folder art, so
// folders use a flat glyph drawn to match. Light mode darkens the palette
// so pale glyphs (white/grey) stay visible on light backgrounds.
import { getIcon } from 'seti-icons'
import type { IconMode, IconProvider } from './index'

const DARK: Record<string, string> = {
  'blue': '#519aba',
  'grey': '#4d5a5e',
  'grey-light': '#6d8086',
  'green': '#8dc149',
  'orange': '#e37933',
  'pink': '#f55385',
  'purple': '#a074c4',
  'red': '#cc3e44',
  'white': '#d4d7d6',
  'yellow': '#cbcb41',
  'ignore': '#41535b',
}

const LIGHT: Record<string, string> = {
  'blue': '#3a7d9e',
  'grey': '#5d6a6e',
  'grey-light': '#66787e',
  'green': '#659b28',
  'orange': '#c96420',
  'pink': '#d63b6e',
  'purple': '#8558ab',
  'red': '#b3373d',
  'white': '#7a8286',
  'yellow': '#9d9d23',
  'ignore': '#9aa6ac',
}

const cache = new Map<string, string>()

// Seti glyphs are drawn with heavy padding (VS Code renders the font at 150%
// to compensate), so used as-is they look tiny next to other themes. Measure
// each glyph's bounding box once and crop the viewBox so the art fills the
// icon like the other sets do.
const TARGET_FILL = 0.8
let measureHost: HTMLDivElement | null = null

const cropViewBox = (svg: string): string => {
  if (typeof document === 'undefined') return svg
  if (!measureHost) {
    measureHost = document.createElement('div')
    measureHost.style.cssText = 'position:fixed;left:-9999px;top:0'
    document.body.appendChild(measureHost)
  }
  measureHost.innerHTML = svg
  let b: DOMRect | undefined
  try {
    b = measureHost.querySelector('svg')?.getBBox()
  } catch { /* jsdom lacks getBBox */ }
  measureHost.innerHTML = ''
  if (!b || b.width <= 0 || b.height <= 0) return svg
  const side = Math.max(b.width, b.height) / TARGET_FILL
  const x = b.x + b.width / 2 - side / 2
  const y = b.y + b.height / 2 - side / 2
  return svg.replace(/viewBox="[^"]*"/, `viewBox="${x} ${y} ${side} ${side}"`)
}

const toUrl = (svg: string, color: string) => {
  const key = `${color}:${svg.length}:${svg.slice(30, 60)}`
  let url = cache.get(key)
  if (!url) {
    const colored = cropViewBox(svg).replace(
      '<svg ',
      `<svg xmlns="http://www.w3.org/2000/svg" fill="${color}" `,
    )
    url = `data:image/svg+xml,${encodeURIComponent(colored)}`
    cache.set(key, url)
  }
  return url
}

const FOLDER =
  '<svg viewBox="0 0 32 32"><path d="M27 8h-9.6l-2.7-3.2A2 2 0 0 0 13.2 4H5a2 2 0 0 0-2 2v20a2 2 0 0 0 2 2h22a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2z"/></svg>'
const FOLDER_OPEN =
  '<svg viewBox="0 0 32 32"><path d="M27 8h-9.6l-2.7-3.2A2 2 0 0 0 13.2 4H5a2 2 0 0 0-2 2v20a2 2 0 0 0 2 2h21.1a2 2 0 0 0 1.9-1.4l3-10A2 2 0 0 0 29.1 14H29v-4a2 2 0 0 0-2-2zM5 6h8.2l3 3.6.6.4H27v4H8.9a2 2 0 0 0-1.9 1.4L5 22.5V6z"/></svg>'

const palette = (mode: IconMode) => (mode === 'light' ? LIGHT : DARK)

const provider: IconProvider = {
  file(name, mode) {
    const { svg, color } = getIcon(name)
    const p = palette(mode)
    return toUrl(svg, p[color] ?? p['grey-light'])
  },
  folder(_name, open, mode) {
    return toUrl(open ? FOLDER_OPEN : FOLDER, palette(mode)['grey-light'])
  },
}

export default provider
