const numberFormat = new Intl.NumberFormat()
const compactFormat = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 })

export function formatCount(n: number): string {
  return numberFormat.format(n)
}

/** 4,266,293 → "4.3M" (for popularity / listener counts). */
export function formatCompact(n: number | null | undefined): string {
  return n === null || n === undefined ? '–' : compactFormat.format(n)
}

export function formatDuration(ms: number | null | undefined): string {
  if (!ms) return ''
  const total = Math.round(ms / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['week', 7 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
]

export function formatRelative(iso: string, now = Date.now()): string {
  const seconds = (new Date(iso).getTime() - now) / 1000
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit)
  }
  return 'just now'
}

/** "every 30 minutes, with a full sync weekly" */
export function describeSchedule(minutes: number, days: number): string {
  const every =
    minutes % 60 === 0
      ? minutes === 60
        ? 'every hour'
        : `every ${minutes / 60} hours`
      : `every ${minutes} minutes`
  const full = days === 1 ? 'daily' : days === 7 ? 'weekly' : `every ${days} days`
  return `Changes are picked up ${every}, with a full sync ${full}.`
}

const dayFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' })
const dateFormat = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

/** "Wed, 18 Nov 2026" in the viewer's time zone. */
export function formatDay(iso: string): string {
  return dayFormat.format(new Date(iso))
}

/** "21:04" in the viewer's time zone. */
export function formatTime(iso: string): string {
  return timeFormat.format(new Date(iso))
}

/** "18 Nov 2026" */
export function formatDate(iso: string): string {
  return dateFormat.format(new Date(iso))
}
