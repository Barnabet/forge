import { describe, expect, it } from 'vitest'
import { fnmatchEscape } from './fnmatch'

describe('fnmatchEscape', () => {
  it('wraps * so it matches literally', () => {
    expect(fnmatchEscape('cat *.log')).toBe('cat [*].log')
  })

  it('wraps [ so a character class matches literally', () => {
    expect(fnmatchEscape('ls [abc]')).toBe('ls [[]abc]')
  })

  it('wraps ?', () => {
    expect(fnmatchEscape('rm a?')).toBe('rm a[?]')
  })

  it('leaves plain strings untouched', () => {
    expect(fnmatchEscape('rm -rf build')).toBe('rm -rf build')
  })
})
