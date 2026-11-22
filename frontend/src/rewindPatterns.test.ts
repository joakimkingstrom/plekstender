import { describe, expect, it } from 'vitest'
import { PATTERN_KINDS, patternSvg, rewindBackground } from './rewindPatterns'

describe('Rewind backgrounds', () => {
  it('draws every kind of pattern as an SVG in the colours given', () => {
    for (const kind of PATTERN_KINDS) {
      const { svg } = patternSvg(kind, '#101010', '#aa3300', '#3355ff', 'albums')
      expect(svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg"')).toBe(true)
      expect(svg).toContain('#101010')
      expect(svg).toMatch(/#aa3300|#3355ff/)
      expect(svg).not.toContain('NaN')
    }
  })

  it('is the same for the same slide, and differs from slide to slide', () => {
    const one = rewindBackground(2, 'albums', '#2850c8', '#e4572e')
    expect(rewindBackground(2, 'albums', '#2850c8', '#e4572e')).toEqual(one)
    expect(rewindBackground(3, 'songs', '#2850c8', '#e4572e').backgroundImage).not.toEqual(one.backgroundImage)
    // Darkened under a veil, so white text stays readable.
    expect(one.backgroundImage.startsWith('linear-gradient(rgba(0,0,0,0.32)')).toBe(true)
    expect(one.backgroundColor).not.toBe('#2850c8')
  })
})
