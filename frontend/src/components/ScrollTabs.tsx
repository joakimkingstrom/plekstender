import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

import { HOLD_MS, moved } from '../reorder'

/** Pixels a pointer moves before a press counts as a drag (scrolling, or giving up a hold). */
const SLOP = 6

/**
 * A row of tabs wider than its space:
 * - drag with the mouse to scroll it sideways (a wheel-less mouse can't otherwise);
 * - hold a tab for a second, then drag it to another place (mouse or touch), or press
 *   Alt + ← / → on a focused tab: `onReorder` gets the new order;
 * - a fade and an arrow at either edge say there's more that way (the arrow scrolls).
 *
 * Each tab is drawn by `renderTab`; the row has the tablist role.
 */
export function ScrollTabs<K extends string>({
  order,
  onReorder,
  renderTab,
  label,
  className = '',
}: {
  order: K[]
  onReorder: (order: K[]) => void
  renderTab: (key: K, state: { dragging: boolean }) => ReactNode
  label: string
  className?: string
}) {
  const bar = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ left: false, right: false })
  const [dragging, setDragging] = useState<K | null>(null)
  const [preview, setPreview] = useState<K[] | null>(null) // the order while dragging
  const shown = preview ?? order
  const press = useRef<{
    id: number
    x: number
    scroll: number
    key: K | null
    timer: number | null
    mode: 'press' | 'scroll' | 'reorder'
  } | null>(null)
  const swallowClick = useRef(false)

  const measure = useCallback(() => {
    const el = bar.current
    if (!el) return
    setEdges({ left: el.scrollLeft > 2, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 2 })
  }, [])
  useLayoutEffect(measure, [measure, shown.length])
  useEffect(() => {
    const el = bar.current
    if (!el) return
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(el)
    return () => observer?.disconnect()
  }, [measure])

  // While a tab is being moved on a touch screen, the page mustn't scroll instead.
  useEffect(() => {
    if (dragging === null) return
    const stop = (e: TouchEvent) => e.preventDefault()
    document.addEventListener('touchmove', stop, { passive: false })
    return () => document.removeEventListener('touchmove', stop)
  }, [dragging])

  const end = () => {
    const p = press.current
    if (p?.timer) window.clearTimeout(p.timer)
    if (p?.mode === 'reorder' && preview && preview.join() !== order.join()) onReorder(preview)
    if (p && p.mode !== 'press') swallowClick.current = true
    press.current = null
    setDragging(null)
    setPreview(null)
  }

  const overIndex = (x: number): number => {
    const tabs = [...(bar.current?.querySelectorAll<HTMLElement>('[data-tab]') ?? [])]
    const i = tabs.findIndex((t) => x < t.getBoundingClientRect().left + t.getBoundingClientRect().width / 2)
    return i === -1 ? tabs.length - 1 : i
  }

  const scrollBy = (direction: 1 | -1) => {
    const el = bar.current
    if (el) el.scrollBy({ left: direction * el.clientWidth * 0.7, behavior: 'smooth' })
  }

  return (
    <div className={`relative ${className}`}>
      <div
        ref={bar}
        role="tablist"
        aria-label={label}
        onScroll={measure}
        className={`no-scrollbar flex gap-1 overflow-x-auto ${dragging !== null ? 'cursor-grabbing select-none' : ''}`}
        onPointerDown={(e) => {
          if (e.button !== 0 || !bar.current) return
          const key = (e.target as HTMLElement).closest<HTMLElement>('[data-tab]')?.dataset.tab as K | undefined
          const p = { id: e.pointerId, x: e.clientX, scroll: bar.current.scrollLeft, key: key ?? null, timer: null as number | null, mode: 'press' as const }
          press.current = p
          if (key)
            p.timer = window.setTimeout(() => {
              if (press.current !== p || p.mode !== 'press') return
              press.current = { ...p, mode: 'reorder' }
              bar.current?.setPointerCapture?.(p.id)
              setDragging(key)
              setPreview(order)
            }, HOLD_MS)
        }}
        onPointerMove={(e) => {
          const p = press.current
          if (!p || p.id !== e.pointerId || !bar.current) return
          const dx = e.clientX - p.x
          if (p.mode === 'reorder' && p.key) {
            setPreview((current) => moved(current ?? order, p.key!, overIndex(e.clientX)))
            return
          }
          if (Math.abs(dx) < SLOP) return
          if (p.mode === 'press') {
            if (p.timer) window.clearTimeout(p.timer) // moved: not a hold
            if (e.pointerType !== 'mouse') {
              press.current = null // touch scrolls by itself
              return
            }
            press.current = { ...p, mode: 'scroll', timer: null }
            bar.current.setPointerCapture?.(e.pointerId)
          }
          bar.current.scrollLeft = p.scroll - dx
        }}
        onPointerUp={end}
        onPointerCancel={end}
        onClickCapture={(e) => {
          // The click that ends a drag isn't a click on a tab.
          if (swallowClick.current) {
            swallowClick.current = false
            e.preventDefault()
            e.stopPropagation()
          }
        }}
        onKeyDown={(e) => {
          if (!e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return
          const key = (e.target as HTMLElement).closest<HTMLElement>('[data-tab]')?.dataset.tab as K | undefined
          if (!key) return
          e.preventDefault()
          const at = order.indexOf(key)
          onReorder(moved(order, key, at + (e.key === 'ArrowRight' ? 1 : -1)))
        }}
      >
        {shown.map((key) => (
          <div key={key} data-tab={key} className={`shrink-0 ${dragging === key ? 'opacity-60' : ''}`}>
            {renderTab(key, { dragging: dragging === key })}
          </div>
        ))}
      </div>
      {edges.left && (
        <button
          type="button"
          aria-label="Scroll the tabs left"
          onClick={() => scrollBy(-1)}
          className="absolute inset-y-0 left-0 flex w-10 items-center justify-start bg-gradient-to-r from-surface via-surface/80 to-transparent pb-2 pl-0.5 text-lg text-muted hover:text-text"
        >
          ‹
        </button>
      )}
      {edges.right && (
        <button
          type="button"
          aria-label="Scroll the tabs right: more tabs"
          onClick={() => scrollBy(1)}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-end bg-gradient-to-l from-surface via-surface/80 to-transparent pb-2 pr-0.5 text-lg text-muted hover:text-text"
        >
          ›
        </button>
      )}
    </div>
  )
}
