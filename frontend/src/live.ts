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

/** A ticket price or range: "Free", "£12", "£35–£75" (in the viewer's number format). */
export function formatPrice(min: number | null | undefined, max: number | null | undefined, currency: string | null | undefined): string | null {
  if (min == null) return null
  const high = max ?? min
  if (min === 0 && high === 0) return 'Free'
  const whole = Number.isInteger(min) && Number.isInteger(high)
  const format = (value: number) => {
    try {
      return currency
        ? new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: whole ? 0 : 2, minimumFractionDigits: whole ? 0 : 2 }).format(value)
        : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)
    } catch {
      return String(value)
    }
  }
  return high > min ? `${format(min)}–${format(high)}` : format(min)
}

/** "2027-03-04" as a local date (no time zone shift). */
export function localDate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

