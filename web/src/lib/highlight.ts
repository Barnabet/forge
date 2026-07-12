// Lazy syntax highlighting via shiki. The library (and each grammar) is only
// loaded on demand, so it stays out of the initial bundle.

const EXT_LANG: Record<string, string> = {
  mjs: 'javascript',
  cjs: 'javascript',
  mts: 'typescript',
  cts: 'typescript',
  h: 'c',
  hpp: 'cpp',
  cc: 'cpp',
  cxx: 'cpp',
}

// Skip huge files: highlighting them is slow and the DOM gets heavy.
const MAX_BYTES = 300_000

/** Returns highlighted HTML, or null when the file type has no grammar. */
export async function highlight(code: string, ext: string): Promise<string | null> {
  if (!ext || code.length > MAX_BYTES) return null
  const { codeToHtml, bundledLanguages } = await import('shiki')
  const lang = EXT_LANG[ext] ?? (ext in bundledLanguages ? ext : null)
  if (!lang) return null
  return codeToHtml(code, {
    lang,
    themes: { dark: 'github-dark-default', light: 'github-light-default' },
    defaultColor: 'dark',
  })
}
