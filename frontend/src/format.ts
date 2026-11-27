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

const TIME_UNITS: [string, number][] = [
  ['y', 525_960], // minutes in a year of 365.25 days
  ['mo', 43_830], // a twelfth of that
  ['w', 10_080],
  ['d', 1_440],
  ['h', 60],
  ['m', 1],
]

/** Listening time in its two biggest units: 5,400,000 ms → "1 h 30 m"; past a day "3 d 4 h",
 * then weeks, months and years ("2 w 3 d", "4 mo 1 w", "1 y 2 mo"). A zero second unit is
 * left out ("2 d"). */
export function formatListeningTime(ms: number): string {
  const minutes = Math.round(ms / 60_000)
  const first = TIME_UNITS.findIndex(([, size]) => minutes >= size)
  if (first === -1 || first === TIME_UNITS.length - 1) return `${minutes} m`
  const [name, size] = TIME_UNITS[first]
  const [nextName, nextSize] = TIME_UNITS[first + 1]
  const whole = Math.floor(minutes / size)
  const rest = Math.floor((minutes - whole * size) / nextSize)
  return rest ? `${formatCount(whole)} ${name} ${rest} ${nextName}` : `${formatCount(whole)} ${name}`
}

/** Change vs a previous value: "+12%", "−8%", or null when there's nothing to compare. */
export function formatChange(current: number, previous: number | null | undefined): string | null {
  if (!previous) return null
  const pct = Math.round(((current - previous) / previous) * 100)
  if (pct === 0) return '±0%'
  return pct > 0 ? `+${pct}%` : `−${Math.abs(pct)}%`
}

/** A share as a percentage that never reads 0% or 100% when it isn't quite. */
export function formatShare(part: number, whole: number): string {
  if (whole <= 0 || part <= 0) return '0%'
  const pct = (part / whole) * 100
  if (pct < 0.1) return '<0.1%'
  if (pct >= 100) return '100%'
  const shown = pct < 10 ? pct.toFixed(1) : String(Math.round(pct))
  return `${shown === '100' ? '99' : shown}%`
}
