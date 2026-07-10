/**
 * Escape a string so Python's `fnmatch` treats every character literally.
 *
 * fnmatch has no escape character, so the wildcard metacharacters `*`, `?` and
 * `[` are neutralized by wrapping each in a single-character class:
 *   `*` → `[*]`, `?` → `[?]`, `[` → `[[]`.
 * A `]` outside a class is already literal in fnmatch, so it needs no escaping.
 */
export function fnmatchEscape(s: string): string {
  return s.replace(/[*?[]/g, m => `[${m}]`)
}
