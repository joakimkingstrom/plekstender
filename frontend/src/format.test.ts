import { describe, expect, it } from 'vitest'
import { formatDuration, formatRelative, formatShare } from './format'

describe('format', () => {
  it('formats track durations', () => {
    expect(formatDuration(383_000)).toBe('6:23')
    expect(formatDuration(61_000)).toBe('1:01')
    expect(formatDuration(null)).toBe('')
  })

  it('formats relative times', () => {
    const now = Date.parse('2026-11-18T12:00:00Z')
    expect(formatRelative('2026-11-18T11:58:00Z', now)).toMatch(/2 minutes ago/)
    expect(formatRelative('2026-11-18T11:59:59Z', now)).toBe('just now')
    expect(formatRelative('2026-11-17T12:00:00Z', now)).toMatch(/yesterday|1 day ago/)
  })

  it('formats shares without rounding to 0% or 100%', () => {
    expect([formatShare(0, 10), formatShare(1, 5000), formatShare(9, 1234), formatShare(1, 3), formatShare(999, 1000), formatShare(5, 5)]).toEqual([
      '0%', '<0.1%', '0.7%', '33%', '99%', '100%',
    ])  // prettier-ignore
  })
})
