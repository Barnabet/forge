export const UI_THEMES = [
  { id: 'dark', label: 'Dark' },
  { id: 'light', label: 'Light' },
  { id: 'system', label: 'System' },
] as const

export type UiTheme = (typeof UI_THEMES)[number]['id']

export function asUiTheme(v: string | null): UiTheme {
  return UI_THEMES.some(t => t.id === v) ? (v as UiTheme) : 'dark'
}

// Lazy: jsdom (tests) has no matchMedia.
const media = () =>
  typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: light)')
    : null

export function resolveUiTheme(theme: UiTheme): 'dark' | 'light' {
  return theme === 'system' ? (media()?.matches ? 'light' : 'dark') : theme
}

// data-theme on <html> drives the token override block in tokens.css.
export function applyUiTheme(theme: UiTheme) {
  document.documentElement.setAttribute('data-theme', resolveUiTheme(theme))
}

// Follow OS changes while in system mode. main.tsx registers this once.
export function watchSystemTheme(getTheme: () => UiTheme) {
  media()?.addEventListener('change', () => {
    if (getTheme() === 'system') applyUiTheme('system')
  })
}
