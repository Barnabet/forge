// Catppuccin icons — pastel palette from catppuccin/vscode-icons.
// The name/extension maps are vendored from that repo's defaults
// (catppuccin-map.json); the SVGs come from @iconify-json/catppuccin.
// The set ships in Macchiato (dark) colors; for light mode each color is
// remapped to its official Latte counterpart.
import set from '@iconify-json/catppuccin/icons.json'
import map from './catppuccin-map.json'
import { makeSvgUrl } from './iconify'
import type { IconMode, IconProvider } from './index'

// Macchiato → Latte, matched by palette role (text, blue, yellow, green…).
const MACCHIATO_TO_LATTE: Record<string, string> = {
  '#cad3f5': '#4c4f69', // text
  '#8aadf4': '#1e66f5', // blue
  '#eed49f': '#df8e1d', // yellow
  '#a6da95': '#40a02b', // green
  '#8087a2': '#6c6f85', // overlay1 / subtext
  '#f5a97f': '#fe640b', // peach
  '#ed8796': '#d20f39', // red
  '#c6a0f6': '#8839ef', // mauve
  '#91d7e3': '#04a5e5', // sky
  '#7dc4e4': '#209fb5', // sapphire
  '#f5bde6': '#ea76cb', // pink
  '#8bd5ca': '#179299', // teal
  '#ee99a0': '#e64553', // maroon
  '#b7bdf8': '#7287fd', // lavender
  '#f4dbd6': '#dc8a78', // rosewater
  '#f0c6c6': '#dd7878', // flamingo
}

const darkUrl = makeSvgUrl(set)
const lightUrl = makeSvgUrl(set, MACCHIATO_TO_LATTE)
const svgUrl = (icon: string, mode: IconMode) =>
  mode === 'light' ? lightUrl(icon) : darkUrl(icon)

const fileNames = map.fileNames as Record<string, string>
const fileExtensions = map.fileExtensions as Record<string, string>
const folderNames = map.folderNames as Record<string, string>

const provider: IconProvider = {
  file(name, mode) {
    const fallback = svgUrl('file', mode)!
    const lower = name.toLowerCase()
    const byName = fileNames[lower]
    if (byName) return svgUrl(byName, mode) ?? fallback
    const parts = lower.split('.')
    for (let i = 1; i < parts.length; i++) {
      const icon = fileExtensions[parts.slice(i).join('.')]
      if (icon) return svgUrl(icon, mode) ?? fallback
    }
    return fallback
  },
  folder(name, open, mode) {
    const base = folderNames[name.toLowerCase()]
    const icon = base ? (open ? `${base}-open` : base) : open ? 'folder-open' : 'folder'
    return svgUrl(icon, mode) ?? svgUrl('file', mode)!
  },
}

export default provider
