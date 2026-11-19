import { describe, expect, it } from 'vitest'
import { accentTokens, applyAccent, contrast, PRESETS } from './accent'

describe('accent colours', () => {
  it('keeps every preset at 3:1 or more against the surface in both themes', () => {
    for (const [, hex] of PRESETS) {
      expect(contrast(accentTokens(hex, 'dark')['--color-chart'], '#1f2025')).toBeGreaterThanOrEqual(3)
      expect(contrast(accentTokens(hex, 'light')['--color-chart'], '#ffffff')).toBeGreaterThanOrEqual(3)
    }
  })

  it('fixes colours that would vanish against the background', () => {
    const navyOnDark = accentTokens('#101030', 'dark')['--color-accent']
    expect(contrast(navyOnDark, '#1f2025')).toBeGreaterThanOrEqual(3)
    const paleOnLight = accentTokens('#fff3a0', 'light')['--color-accent']
    expect(contrast(paleOnLight, '#ffffff')).toBeGreaterThanOrEqual(3)
  })

  it('picks readable text on the accent', () => {
    expect(accentTokens('#ffc53d', 'dark')['--color-on-accent']).toBe('#1c1d21')
    expect(accentTokens('#3e63dd', 'light')['--color-on-accent']).toBe('#ffffff')
  })

  it('sets and clears the CSS variables', () => {
    const root = document.createElement('div')
    applyAccent('#3e63dd', 'light', root)
    expect(root.style.getPropertyValue('--color-accent')).toBeTruthy()
    applyAccent(null, 'light', root)
    expect(root.style.getPropertyValue('--color-accent')).toBe('')
  })
})
