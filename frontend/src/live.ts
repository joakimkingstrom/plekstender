/** Same rule as the server: the top % of played artists, at least one, at most the cap. */
export function followCount(listened: number, percent: number, max: number): number {
  if (listened <= 0) return 0
  return Math.max(1, Math.min(max, Math.ceil((listened * percent) / 100)))
}
