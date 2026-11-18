import { describe, expect, it } from 'vitest'
import { starsText, swipeResult } from './swipe'

describe('swipeResult', () => {
  it('maps distance to rating: right 2.5-5 stars, left 2-0.5 stars', () => {
    expect(swipeResult(10, 0, 360)).toEqual({ kind: 'none' })
    expect(swipeResult(30, 0, 360)).toEqual({ kind: 'rate', rating: 5 })
    expect(swipeResult(400, 0, 360)).toEqual({ kind: 'rate', rating: 10 })
    expect(swipeResult(-30, 0, 360)).toEqual({ kind: 'rate', rating: 4 })
    expect(swipeResult(-400, 0, 360)).toEqual({ kind: 'rate', rating: 1 })
    const ratings = [40, 80, 120, 160, 200, 240, 280, 320].map((dx) => swipeResult(dx, 0, 360))
    const values = ratings.map((r) => (r.kind === 'rate' ? r.rating : 0))
    expect(values).toEqual([...values].sort((a, b) => a - b)) // further = higher
  })

  it('swiping down skips, unless it is mostly sideways', () => {
    expect(swipeResult(10, 120, 360)).toEqual({ kind: 'skip' })
    expect(swipeResult(150, 120, 360).kind).toBe('rate')
    expect(swipeResult(0, 50, 360)).toEqual({ kind: 'none' })
  })
})

describe('starsText', () => {
  it('writes half stars', () => {
    expect(starsText(7)).toBe('3½')
    expect(starsText(1)).toBe('½')
    expect(starsText(10)).toBe('5')
  })
})
