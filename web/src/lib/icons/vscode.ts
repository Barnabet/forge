// vscode-icons — the classic VS Code extension set.
// vscode-icons-js resolves names to "file_type_x.svg"; the SVGs themselves
// come from @iconify-json/vscode-icons under "file-type-x" keys. The set
// carries explicit "file-type-light-x" variants for light backgrounds.
import set from '@iconify-json/vscode-icons/icons.json'
import {
  DEFAULT_FILE,
  DEFAULT_FOLDER,
  DEFAULT_FOLDER_OPENED,
  getIconForFile,
  getIconForFolder,
  getIconForOpenFolder,
} from 'vscode-icons-js'
import { makeSvgUrl } from './iconify'
import type { IconMode, IconProvider } from './index'

const svgUrl = makeSvgUrl(set)

// The iconify set renamed one icon; everything else maps 1:1.
const RENAMED: Record<string, string> = { 'file-type-pdf': 'file-type-pdf2' }

const toKey = (fileName: string) => {
  const key = fileName.replace(/\.svg$/, '').replace(/_/g, '-')
  return RENAMED[key] ?? key
}

// In light mode prefer the set's dedicated light variant when one exists.
const themed = (key: string, mode: IconMode) => {
  if (mode === 'light' && key.startsWith('file-type-') && !key.startsWith('file-type-light-')) {
    const light = key.replace('file-type-', 'file-type-light-')
    const url = svgUrl(light)
    if (url) return url
  }
  return svgUrl(key)
}

const provider: IconProvider = {
  file(name, mode) {
    // The lookup tables are keyed lowercase ("makefile", not "Makefile").
    const key = toKey(getIconForFile(name.toLowerCase()) ?? DEFAULT_FILE)
    return themed(key, mode) ?? svgUrl(toKey(DEFAULT_FILE))!
  },
  folder(name, open, mode) {
    void mode // folder art works on both backgrounds
    const lower = name.toLowerCase()
    const icon = open
      ? getIconForOpenFolder(lower) ?? DEFAULT_FOLDER_OPENED
      : getIconForFolder(lower) ?? DEFAULT_FOLDER
    return svgUrl(toKey(icon)) ?? svgUrl(toKey(DEFAULT_FILE))!
  },
}

export default provider
