// Material file icons (VS Code's material-icon-theme set).
// The manifest maps file names/extensions and folder names to icon ids;
// the glob makes Vite emit each SVG as a URL asset, fetched only when rendered.
// The "light" block lists overrides for icons that need a variant on light
// backgrounds (white/pale logos); everything else keeps its base icon.
import manifest from 'material-icon-theme/dist/material-icons.json'
import type { IconMode, IconProvider } from './index'

// no-inline: without it Vite data-URI-inlines all ~1250 SVGs into the bundle.
const urls = import.meta.glob('/node_modules/material-icon-theme/icons/*.svg', {
  query: '?url&no-inline',
  import: 'default',
  eager: true,
}) as Record<string, string>

const url = (icon: string) =>
  urls[`/node_modules/material-icon-theme/icons/${icon}.svg`] ??
  urls[`/node_modules/material-icon-theme/icons/${manifest.file}.svg`]

type Map = Record<string, string>
const base = manifest as unknown as {
  fileNames: Map; fileExtensions: Map; folderNames: Map; folderNamesExpanded: Map
  light: { fileNames: Map; fileExtensions: Map; folderNames: Map; folderNamesExpanded: Map }
}

const lookup = (maps: Map[], key: string): string | undefined => {
  for (const m of maps) { const v = m[key]; if (v && v in urlsIndex) return v }
  return undefined
}

// Index of icon ids that actually shipped (some light clones are absent).
const urlsIndex: Record<string, true> = {}
for (const p of Object.keys(urls))
  urlsIndex[p.slice(p.lastIndexOf('/') + 1, -4)] = true

const fileMaps = (mode: IconMode) =>
  mode === 'light' ? [base.light.fileNames, base.fileNames] : [base.fileNames]
const extMaps = (mode: IconMode) =>
  mode === 'light' ? [base.light.fileExtensions, base.fileExtensions] : [base.fileExtensions]

const provider: IconProvider = {
  file(name, mode) {
    const lower = name.toLowerCase()
    const byName = lookup(fileMaps(mode), lower)
    if (byName) return url(byName)
    // Longest compound extension wins, e.g. "d.ts" before "ts".
    const parts = lower.split('.')
    for (let i = 1; i < parts.length; i++) {
      const icon = lookup(extMaps(mode), parts.slice(i).join('.'))
      if (icon) return url(icon)
    }
    return url(manifest.file)
  },
  folder(name, open, mode) {
    const lower = name.toLowerCase()
    const maps = open
      ? mode === 'light' ? [base.light.folderNamesExpanded, base.folderNamesExpanded] : [base.folderNamesExpanded]
      : mode === 'light' ? [base.light.folderNames, base.folderNames] : [base.folderNames]
    return url(lookup(maps, lower) ?? (open ? manifest.folderExpanded : manifest.folder))
  },
}

export default provider
