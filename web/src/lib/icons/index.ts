import { useEffect, useState } from 'react'

export type IconMode = 'dark' | 'light'

export interface IconProvider {
  file(name: string, mode: IconMode): string
  folder(name: string, open: boolean, mode: IconMode): string
}

export const ICON_THEMES = [
  { id: 'material', label: 'Material' },
  { id: 'catppuccin', label: 'Catppuccin' },
  { id: 'vscode', label: 'VSCode' },
  { id: 'seti', label: 'Seti' },
] as const

export type IconThemeId = (typeof ICON_THEMES)[number]['id']

export const DEFAULT_ICON_THEME: IconThemeId = 'material'

export function asIconTheme(v: string | null): IconThemeId {
  return ICON_THEMES.some(t => t.id === v) ? (v as IconThemeId) : DEFAULT_ICON_THEME
}

// Each theme is a separate chunk: the manifests are large (vscode-icons'
// iconify JSON alone is ~3.6MB) so they load only when selected.
const loaders: Record<IconThemeId, () => Promise<{ default: IconProvider }>> = {
  material: () => import('./material'),
  catppuccin: () => import('./catppuccin'),
  vscode: () => import('./vscode'),
  seti: () => import('./seti'),
}

const cache: Partial<Record<IconThemeId, IconProvider>> = {}

export async function loadIconProvider(id: IconThemeId): Promise<IconProvider> {
  return (cache[id] ??= (await loaders[id]()).default)
}

export function useIconProvider(id: IconThemeId): IconProvider | null {
  const [provider, setProvider] = useState<IconProvider | null>(cache[id] ?? null)
  useEffect(() => {
    let live = true
    void loadIconProvider(id)
      .then(p => { if (live) setProvider(p) })
      .catch(() => undefined)
    return () => { live = false }
  }, [id])
  return provider
}
