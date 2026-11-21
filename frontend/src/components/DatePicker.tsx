import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'

/** Dates as the API has them: YYYY-MM-DD, in local time. */
type Ymd = string

const pad = (n: number) => String(n).padStart(2, '0')
const toYmd = (d: Date): Ymd => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const fromYmd = (s: Ymd): Date | null => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null
}
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)
/** The same day in another month, or that month's last day when it's shorter. */
const addMonths = (d: Date, n: number) => {
  const last = new Date(d.getFullYear(), d.getMonth() + n + 1, 0).getDate()
  return new Date(d.getFullYear(), d.getMonth() + n, Math.min(d.getDate(), last))
}

const buttonFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
const fullFormat = new Intl.DateTimeFormat(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
const titleFormat = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' })
const monthFormat = new Intl.DateTimeFormat(undefined, { month: 'short' })
const weekdayFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short' })
// Monday first, as everywhere on the site. 2026-11-16 is a Monday.
const WEEKDAYS = Array.from({ length: 7 }, (_, i) => weekdayFormat.format(new Date(2026, 10, 16 + i)))
const MONTHS = Array.from({ length: 12 }, (_, i) => monthFormat.format(new Date(2026, i, 1)))

type View = 'days' | 'months' | 'years'

/**
 * A site-styled date picker in place of the browser's <input type="date">: a button that
 * opens a calendar. The title switches to months, then years, for dates far away.
 * Keyboard: ← → a day, ↑ ↓ a week, Page Up / Down a month (with Shift a year), Home / End
 * the week's start and end, Enter picks, Esc closes.
 */
export function DatePicker({
  value,
  onChange,
  min,
  max,
  label,
  labelledBy,
  placeholder = 'Pick a date',
  clearable = false,
  className = '',
  size = 'md',
}: {
  value: Ymd
  onChange: (value: Ymd) => void
  min?: Ymd
  max?: Ymd
  /** Accessible name when there's no visible label (use `labelledBy` when there is). */
  label?: string
  labelledBy?: string
  placeholder?: string
  clearable?: boolean
  className?: string
  size?: 'sm' | 'md'
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<View>('days')
  const [today] = useState(() => toYmd(new Date()))
  const [active, setActive] = useState<Date>(() => fromYmd(value) ?? new Date())
  const root = useRef<HTMLDivElement>(null)
  const grid = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const selected = fromYmd(value)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  // Keep focus on the active day as it moves.
  useEffect(() => {
    if (open && view === 'days') grid.current?.querySelector<HTMLElement>(`[data-day="${toYmd(active)}"]`)?.focus()
  }, [open, view, active])

  const allowed = (d: Ymd) => (!min || d >= min) && (!max || d <= max)
  const clamp = (d: Date) => {
    const s = toYmd(d)
    if (min && s < min) return fromYmd(min)!
    if (max && s > max) return fromYmd(max)!
    return d
  }
  const openPicker = () => {
    setActive(clamp(selected ?? new Date()))
    setView('days')
    setOpen(true)
  }
  const close = () => {
    setOpen(false)
    button.current?.focus()
  }
  const pick = (d: Date) => {
    const s = toYmd(d)
    if (!allowed(s)) return
    onChange(s)
    close()
  }

  const onGridKey = (e: KeyboardEvent) => {
    const moves: Record<string, () => Date> = {
      ArrowLeft: () => addDays(active, -1),
      ArrowRight: () => addDays(active, 1),
      ArrowUp: () => addDays(active, -7),
      ArrowDown: () => addDays(active, 7),
      PageUp: () => addMonths(active, e.shiftKey ? -12 : -1),
      PageDown: () => addMonths(active, e.shiftKey ? 12 : 1),
      Home: () => addDays(active, -((active.getDay() + 6) % 7)),
      End: () => addDays(active, 6 - ((active.getDay() + 6) % 7)),
    }
    if (moves[e.key]) setActive(clamp(moves[e.key]()))
    else if (e.key === 'Enter' || e.key === ' ') pick(active)
    else if (e.key === 'Escape') close()
    else return
    e.preventDefault()
  }

  // Six weeks from the Monday on or before the 1st.
  const first = new Date(active.getFullYear(), active.getMonth(), 1)
  const start = addDays(first, -((first.getDay() + 6) % 7))
  const days = Array.from({ length: 42 }, (_, i) => addDays(start, i))
  const decade = Math.floor(active.getFullYear() / 12) * 12
  const padding = size === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-3 py-2 text-sm'
  const nav = 'flex size-8 items-center justify-center rounded-md text-muted hover:bg-surface-2 hover:text-text disabled:opacity-30'
  const step = (by: number) =>
    setActive((d) => (view === 'days' ? addMonths(d, by) : view === 'months' ? addMonths(d, by * 12) : addMonths(d, by * 144)))
  const title =
    view === 'days' ? titleFormat.format(active) : view === 'months' ? String(active.getFullYear()) : `${decade} – ${decade + 11}`

  return (
    <div ref={root} className={`relative inline-block ${className}`}>
      <button
        ref={button}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={label}
        aria-labelledby={labelledBy ? `${labelledBy} ${id}-value` : undefined}
        onClick={() => (open ? close() : openPicker())}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            openPicker()
          }
        }}
        className={`flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-surface-2 text-left hover:border-muted ${padding}`}
      >
        <span id={`${id}-value`} className={`truncate ${selected ? '' : 'text-muted'}`}>
          {selected ? buttonFormat.format(selected) : placeholder}
        </span>
        <svg viewBox="0 0 16 16" className="size-4 shrink-0 text-muted" aria-hidden>
          <rect x="2" y="3" width="12" height="11" rx="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={label ?? 'Choose a date'}
          className="absolute left-0 z-40 mt-1 w-72 rounded-xl border border-border bg-surface p-3 shadow-xl"
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              close()
            }
          }}
        >
          <div className="mb-2 flex items-center justify-between gap-1">
            <button type="button" className={nav} onClick={() => step(-1)} aria-label="Earlier">
              ‹
            </button>
            <button
              type="button"
              className="rounded-md px-2 py-1 font-display text-sm font-semibold capitalize hover:bg-surface-2 disabled:hover:bg-transparent"
              onClick={() => setView(view === 'days' ? 'months' : 'years')}
              disabled={view === 'years'}
              aria-live="polite"
            >
              {title}
            </button>
            <button type="button" className={nav} onClick={() => step(1)} aria-label="Later">
              ›
            </button>
          </div>

          {view === 'days' && (
            <div ref={grid} role="grid" aria-label={titleFormat.format(active)} onKeyDown={onGridKey}>
              <div role="row" className="grid grid-cols-7 text-center text-[11px] font-semibold uppercase tracking-wide text-muted">
                {WEEKDAYS.map((d) => (
                  <span key={d} role="columnheader" className="py-1">
                    {d}
                  </span>
                ))}
              </div>
              {Array.from({ length: 6 }, (_, week) => (
                <div key={week} role="row" className="grid grid-cols-7">
                  {days.slice(week * 7, week * 7 + 7).map((d) => {
                    const s = toYmd(d)
                    const inMonth = d.getMonth() === active.getMonth()
                    const isSelected = s === value
                    const isActive = s === toYmd(active)
                    return (
                      <span key={s} role="gridcell" aria-selected={isSelected}>
                        <button
                          type="button"
                          data-day={s}
                          tabIndex={isActive ? 0 : -1}
                          disabled={!allowed(s)}
                          aria-label={fullFormat.format(d)}
                          aria-current={s === today ? 'date' : undefined}
                          onClick={() => pick(d)}
                          className={`m-0.5 flex h-8 w-[calc(100%-4px)] items-center justify-center rounded-lg text-sm tabular-nums transition-colors disabled:cursor-not-allowed disabled:opacity-30 ${
                            isSelected
                              ? 'bg-accent font-semibold text-on-accent'
                              : `${inMonth ? 'text-text' : 'text-muted/60'} hover:bg-surface-2 ${s === today ? 'ring-1 ring-inset ring-accent' : ''}`
                          }`}
                        >
                          {d.getDate()}
                        </button>
                      </span>
                    )
                  })}
                </div>
              ))}
            </div>
          )}

          {view === 'months' && (
            <div className="grid grid-cols-3 gap-1">
              {MONTHS.map((m, i) => {
                const isCurrent = selected?.getFullYear() === active.getFullYear() && selected.getMonth() === i
                return (
                  <button
                    key={m}
                    type="button"
                    onClick={() => {
                      setActive(clamp(new Date(active.getFullYear(), i, Math.min(active.getDate(), new Date(active.getFullYear(), i + 1, 0).getDate()))))
                      setView('days')
                    }}
                    className={`rounded-lg py-2.5 text-sm capitalize ${isCurrent ? 'bg-accent font-semibold text-on-accent' : 'hover:bg-surface-2'}`}
                  >
                    {m}
                  </button>
                )
              })}
            </div>
          )}

          {view === 'years' && (
            <div className="grid grid-cols-3 gap-1">
              {Array.from({ length: 12 }, (_, i) => decade + i).map((y) => (
                <button
                  key={y}
                  type="button"
                  onClick={() => {
                    setActive(addMonths(active, (y - active.getFullYear()) * 12))
                    setView('months')
                  }}
                  className={`rounded-lg py-2.5 text-sm tabular-nums ${selected?.getFullYear() === y ? 'bg-accent font-semibold text-on-accent' : 'hover:bg-surface-2'}`}
                >
                  {y}
                </button>
              ))}
            </div>
          )}

          <div className="mt-2 flex justify-between border-t border-border pt-2 text-sm">
            <button
              type="button"
              className="rounded-md px-2 py-1 text-accent hover:bg-surface-2 disabled:opacity-40"
              disabled={!allowed(today)}
              onClick={() => pick(new Date())}
            >
              Today
            </button>
            {clearable && value && (
              <button
                type="button"
                className="rounded-md px-2 py-1 text-muted hover:bg-surface-2 hover:text-text"
                onClick={() => {
                  onChange('')
                  close()
                }}
              >
                Clear
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
