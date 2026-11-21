import { describe, expect, it } from 'vitest'
import { layoutCloud } from './wordCloud'

const WORDS = 'Love Time Man Night Heart World Like Get Home Life Back Good Song Day Way Go Girl Fire Black Blues Know Last Little Nothing Dead Eyes Never Light Dream Death Blood Dance Again Rock Take Want Sweet Right Woman Long Lost Make Mind More Only New Now Old Say See Sun Too Let God Got End Bad Boy Away Baby Blue City Come Feel Gone Have High Something Things Another Gonna Morning Better Through Heaven Tonight Rain Road Roll Dark Days Fall Give Head Hell Keep Moon Need Red Run Sky War Big Still Thing Wanna White Young'
  .split(' ')
  .map((word, i) => ({ word, count: Math.round(400 / (i + 4)) }))
const measure = (text: string, size: number) => text.length * size * 0.55

describe('layoutCloud', () => {
  it('places every word inside the width without overlaps, the same way every time', () => {
    const { placed, height } = layoutCloud(WORDS, 900, measure)
    expect(placed).toHaveLength(WORDS.length)
    for (const p of placed) {
      expect(p.x - p.w / 2).toBeGreaterThanOrEqual(0)
      expect(p.x + p.w / 2).toBeLessThanOrEqual(900)
      expect(p.y - p.h / 2).toBeGreaterThanOrEqual(-0.01)
      expect(p.y + p.h / 2).toBeLessThanOrEqual(height + 0.01)
    }
    for (const [i, a] of placed.entries())
      for (const b of placed.slice(i + 1))
        expect(Math.abs(a.x - b.x) * 2 >= a.w + b.w || Math.abs(a.y - b.y) * 2 >= a.h + b.h).toBe(true)
    expect(layoutCloud(WORDS, 900, measure)).toEqual({ placed, height })
    // The biggest word sits in the middle, and some words are turned.
    expect(placed[0]).toMatchObject({ word: 'Love', tier: 2 })
    expect(Math.abs(placed[0].x - 450)).toBeLessThan(60)
    expect(placed.some((p) => p.vertical)).toBe(true)
  })

  it('fits a phone too, just taller', () => {
    const wide = layoutCloud(WORDS, 900, measure)
    const narrow = layoutCloud(WORDS, 340, measure)
    expect(narrow.placed.length).toBeGreaterThan(WORDS.length * 0.9)
    expect(narrow.height).toBeGreaterThan(wide.height)
  })
})
