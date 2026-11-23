import { describe, expect, it } from 'vitest'
import { wasUpdated } from './version'

describe('wasUpdated', () => {
  it('is true when the server runs a newer build than the page', () => {
    expect(wasUpdated('0.1.210', '0.1.212')).toBe(true)
  })
  it('is false for the same build, a development page or server, or no answer yet', () => {
    expect(wasUpdated('0.1.212', '0.1.212')).toBe(false)
    expect(wasUpdated(null, '0.1.212')).toBe(false)
    expect(wasUpdated('0.1.212', '0.1.0-dev')).toBe(false)
    expect(wasUpdated('0.1.212', undefined)).toBe(false)
  })
})
