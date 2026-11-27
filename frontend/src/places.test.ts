import { describe, expect, it } from 'vitest'
import { countryFlag } from './places'

describe('countryFlag', () => {
  it('turns a two-letter code into a flag', () => {
    expect(countryFlag('GB')).toBe('🇬🇧')
    expect(countryFlag('se')).toBe('🇸🇪')
  })
  it('leaves anything else out', () => {
    expect(countryFlag('')).toBe('')
    expect(countryFlag('GBR')).toBe('')
    expect(countryFlag('1A')).toBe('')
  })
})
