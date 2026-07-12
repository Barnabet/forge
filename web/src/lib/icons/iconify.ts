// Renders icons from an Iconify JSON set to data: URLs usable in <img src>.
import { getIconData, iconToSVG } from '@iconify/utils'

// @iconify/types isn't a direct dependency; borrow the JSON-set type
// from getIconData's signature instead.
type IconifyJSON = Parameters<typeof getIconData>[0]

export function makeSvgUrl(
  set: IconifyJSON,
  recolor?: Record<string, string>,
): (icon: string) => string | undefined {
  const cache = new Map<string, string | undefined>()
  const pattern = recolor && new RegExp(Object.keys(recolor).join('|'), 'gi')
  return icon => {
    if (cache.has(icon)) return cache.get(icon)
    const data = getIconData(set, icon)
    let result: string | undefined
    if (data) {
      const r = iconToSVG(data)
      const attrs = Object.entries(r.attributes)
        .map(([k, v]) => `${k}="${v}"`)
        .join(' ')
      let body = r.body
      if (pattern) body = body.replace(pattern, m => recolor![m.toLowerCase()] ?? m)
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" ${attrs}>${body}</svg>`
      result = `data:image/svg+xml,${encodeURIComponent(svg)}`
    }
    cache.set(icon, result)
    return result
  }
}
