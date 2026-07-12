import { describe, expect, it } from 'vitest'
import { formatLastTs } from './time'

const NOW = new Date('2026-07-11T15:00:00').getTime() / 1000

describe('formatLastTs', () => {
  it('returns empty for unknown', () => {
    expect(formatLastTs(0, NOW)).toBe('')
  })
  it('under a minute → now', () => {
    expect(formatLastTs(NOW - 30, NOW)).toBe('now')
  })
  it('under an hour → minutes', () => {
    expect(formatLastTs(NOW - 5 * 60, NOW)).toBe('5m')
  })
  it('same day → clock time', () => {
    expect(formatLastTs(NOW - 3 * 3600, NOW)).toMatch(/12/)
  })
  it('within a week → days', () => {
    expect(formatLastTs(NOW - 2 * 86400, NOW)).toBe('2d')
  })
  it('older → short date', () => {
    expect(formatLastTs(NOW - 30 * 86400, NOW)).toMatch(/Jun/)
  })
})
