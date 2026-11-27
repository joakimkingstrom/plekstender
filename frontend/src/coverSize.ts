import { useState } from 'react'

/** How big covers are in a grid (Releases, the library, an artist's albums). */
export type CoverSize = 'small' | 'medium' | 'large'

export const COVER_SIZES: [CoverSize, string][] = [
  ['small', 'S'],
  ['medium', 'M'],
  ['large', 'L'],
]

/** Pixels asked of the server for a cover at each size (sharp on high-density screens). */
export const COVER_PIXELS: Record<CoverSize, number> = { small: 256, medium: 256, large: 512 }

/** A grid's cover size, remembered in this browser under `key` (each page its own). */
export function useCoverSize(key: string): [CoverSize, (size: CoverSize) => void] {
  const [size, setSize] = useState<CoverSize>(() => {
    try {
      const stored = localStorage.getItem(key)
      return COVER_SIZES.some(([s]) => s === stored) ? (stored as CoverSize) : 'medium'
    } catch {
      return 'medium'
    }
  })
  const change = (next: CoverSize) => {
    setSize(next)
    try {
      localStorage.setItem(key, next)
    } catch {
      // private browsing: just for now
    }
  }
  return [size, change]
}
