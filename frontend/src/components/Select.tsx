import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'

export type SelectOption<T extends string | number> = { value: T; label: string; group?: string }

/** The list's most height (px), and its gap from the button. */
const LIST_MAX = 288
const GAP = 4
const OPTION_HEIGHT = 34 // about, for working out whether the list fits below

/** The visible area an element can draw in: the window, narrowed by every ancestor that
 * scrolls or clips its overflow. */
function clipBounds(el: HTMLElement): { top: number; bottom: number } {
  let top = 0
  let bottom = window.innerHeight
  for (let node = el.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node)
    if (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'hidden' || overflowY === 'clip') {
      const r = node.getBoundingClientRect()
      top = Math.max(top, r.top)
      bottom = Math.min(bottom, r.bottom)
    }
  }
  return { top, bottom }
}

/**
 * A site-styled dropdown (button + listbox) in place of the browser's <select>. It opens
 * downwards, or upwards when there's more room above (near the bottom of the window), and
 * never makes the page scroll.
 * Keyboard: Enter / Space / ↓ open it; ↑ ↓ Home End move; Enter or Space picks; Esc or Tab
 * closes; typing jumps to the first option starting with those letters.
 */
export function Select<T extends string | number>({
  value,
  options,
  onChange,
  label,
  labelledBy,
  className = '',
  size = 'md',
}: {
  value: T
  options: SelectOption<T>[]
  onChange: (value: T) => void
  /** Accessible name when there's no visible label (use `labelledBy` when there is). */
  label?: string
  labelledBy?: string
  className?: string
  size?: 'sm' | 'md'
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [place, setPlace] = useState<{ up: boolean; max: number }>({ up: false, max: LIST_MAX })
  const [active, setActive] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const typed = useRef({ text: '', at: 0 })
  const selectedIndex = Math.max(0, options.findIndex((o) => o.value === value))
  const selected = options[selectedIndex]

  // Close when clicking anywhere else.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  // Keep the active option in view, scrolling the list only (not the page).
  useEffect(() => {
    const ul = list.current
    const option = ul?.querySelector<HTMLElement>(`[data-index="${active}"]`)
    if (!open || !ul || !option) return
    if (option.offsetTop < ul.scrollTop) ul.scrollTop = option.offsetTop
    else if (option.offsetTop + option.offsetHeight > ul.scrollTop + ul.clientHeight)
      ul.scrollTop = option.offsetTop + option.offsetHeight - ul.clientHeight
  }, [open, active])

  const openList = () => {
    // Below, unless it doesn't fit there and there's more room above.
    const rect = button.current?.getBoundingClientRect()
    if (rect) {
      const wanted = Math.min(LIST_MAX, options.length * OPTION_HEIGHT + 8)
      // The room it has: the window's, or less inside something that scrolls or clips (a
      // dialog's body), which would cut the list off.
      const bounds = clipBounds(button.current!)
      const below = bounds.bottom - rect.bottom - GAP * 2
      const above = rect.top - bounds.top - GAP * 2
      const up = below < wanted && above > below
      setPlace({ up, max: Math.max(120, Math.min(LIST_MAX, up ? above : below)) })
    }
    setActive(selectedIndex)
    setOpen(true)
    requestAnimationFrame(() => list.current?.focus({ preventScroll: true }))
  }
  const close = () => {
    setOpen(false)
    button.current?.focus()
  }
  const pick = (index: number) => {
    const option = options[index]
    if (option) onChange(option.value)
    close()
  }

  const typeahead = (key: string) => {
    const now = Date.now()
    typed.current = { text: (now - typed.current.at < 700 ? typed.current.text : '') + key.toLowerCase(), at: now }
    const found = options.findIndex((o) => o.label.toLowerCase().startsWith(typed.current.text))
    if (found >= 0) setActive(found)
  }

  const onButtonKey = (e: KeyboardEvent) => {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
      e.preventDefault()
      openList()
    }
  }

  const onListKey = (e: KeyboardEvent) => {
    const last = options.length - 1
    if (e.key === 'ArrowDown') setActive((i) => Math.min(last, i + 1))
    else if (e.key === 'ArrowUp') setActive((i) => Math.max(0, i - 1))
    else if (e.key === 'Home') setActive(0)
    else if (e.key === 'End') setActive(last)
    else if (e.key === 'Enter' || e.key === ' ') pick(active)
    else if (e.key === 'Escape') close()
    else if (e.key === 'Tab') setOpen(false)
    else if (e.key.length === 1) typeahead(e.key)
    else return
    if (e.key !== 'Tab') e.preventDefault()
  }

  const padding = size === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-3 py-2 text-sm'
  // A group heading before the first option of each group.
  const headings = options.map((o, i) => (o.group && o.group !== options[i - 1]?.group ? o.group : null))

  return (
    <div ref={root} className={`relative inline-block ${className}`}>
      <button
        ref={button}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        aria-labelledby={labelledBy ? `${labelledBy} ${id}-value` : undefined}
        onClick={() => (open ? close() : openList())}
        onKeyDown={onButtonKey}
        className={`flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-surface-2 text-left hover:border-muted ${padding}`}
      >
        <span id={`${id}-value`} className="truncate">
          {selected?.label ?? ''}
        </span>
        <svg viewBox="0 0 12 12" className={`size-3 shrink-0 text-muted transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden>
          <path d="M2 4.5l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <ul
          ref={list}
          role="listbox"
          tabIndex={-1}
          aria-label={label}
          aria-labelledby={labelledBy}
          aria-activedescendant={`${id}-${active}`}
          onKeyDown={onListKey}
          style={{ maxHeight: place.max }}
          className={`absolute left-0 z-40 min-w-full overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-xl focus:outline-none ${
            place.up ? 'bottom-full mb-1' : 'top-full mt-1'
          }`}
        >
          {options.map((option, index) => {
            const heading = headings[index]
            return (
              <li key={String(option.value)} role="presentation">
                {heading && (
                  <div role="presentation" className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
                    {heading}
                  </div>
                )}
                <div
                  id={`${id}-${index}`}
                  data-index={index}
                  role="option"
                  aria-selected={option.value === value}
                  onMouseEnter={() => setActive(index)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(index)}
                  className={`flex cursor-pointer items-center justify-between gap-3 whitespace-nowrap rounded-md px-2 py-1.5 text-sm ${
                    index === active ? 'bg-surface-2' : ''
                  }`}
                >
                  {option.label}
                  {option.value === value && <span className="text-accent" aria-hidden>✓</span>}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
