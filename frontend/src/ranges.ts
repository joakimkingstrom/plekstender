/** Stats periods: the fixed ones, then each year since the first listen. */
export const FIXED_RANGES: [string, string][] = [
  ['7d', '7 days'],
  ['30d', '30 days'],
  ['365d', '12 months'],
]

export function rangeOptions(firstListen: string | null | undefined): [string, string][] {
  const thisYear = new Date().getFullYear()
  const firstYear = firstListen ? new Date(firstListen).getFullYear() : thisYear
  const years: [string, string][] = []
  for (let y = thisYear; y >= firstYear; y--) years.push([`y${y}`, String(y)])
  return [['all', 'All time'], ...FIXED_RANGES, ...years]
}
