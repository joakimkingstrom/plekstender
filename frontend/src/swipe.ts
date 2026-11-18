/**
 * Swipe-to-rate: right for 2.5-5 stars, left for 2-0.5 stars (the further, the higher or
 * lower), down to skip. Ratings are on Plex's 0-10 scale (half stars = 1).
 */
export type SwipeResult = { kind: 'rate'; rating: number } | { kind: 'skip' } | { kind: 'none' }

/** Movement before a swipe counts. */
export const SWIPE_THRESHOLD = 24
const SKIP_DISTANCE = 90

export function swipeResult(dx: number, dy: number, width: number): SwipeResult {
  const span = Math.max(SWIPE_THRESHOLD + 40, Math.min(width * 0.9, 320))
  if (dy > SKIP_DISTANCE && Math.abs(dx) < dy * 0.6) return { kind: 'skip' }
  if (Math.abs(dx) < SWIPE_THRESHOLD) return { kind: 'none' }
  const f = Math.min(1, (Math.abs(dx) - SWIPE_THRESHOLD) / (span - SWIPE_THRESHOLD))
  if (dx > 0) return { kind: 'rate', rating: 5 + Math.round(f * 5) } // 2.5-5 stars
  return { kind: 'rate', rating: 4 - Math.round(f * 3) } // 2-0.5 stars
}

/** "3½" for 7 on the 0-10 scale. */
export function starsText(rating: number): string {
  const whole = Math.floor(rating / 2)
  return rating % 2 ? `${whole || ''}½` : String(whole)
}
