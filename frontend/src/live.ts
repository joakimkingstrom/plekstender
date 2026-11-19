/** Same rule as the server: the top % of played artists (at least one). */
export function followCount(listened: number, percent: number): number {
  if (listened <= 0) return 0
  return Math.max(1, Math.ceil((listened * percent) / 100))
}

/** A listening period in months, as words: 3 → "3 months", 24 → "2 years". */
export function describePeriod(months: number | null | undefined): string {
  if (!months) return 'all time'
  if (months % 12 === 0) return months === 12 ? 'the past year' : `the past ${months / 12} years`
  return months === 1 ? 'the past month' : `the past ${months} months`
}
