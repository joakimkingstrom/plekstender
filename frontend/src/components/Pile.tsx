import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Button } from './ui'

/** A steady bit of untidiness per item (the same every time): how it lies in the pile. */
function jitter(id: string) {
  let h = 2166136261
  for (const c of id) h = Math.imul(h ^ c.charCodeAt(0), 16777619)
  const r = (shift: number) => (((h >>> shift) & 0xff) / 255) * 2 - 1
  return { rotate: r(0) * 13, x: r(8) * 34, y: r(16) * 14 }
}

const SHOWN = 6 // items visible in the pile; the rest sit hidden under the bottom one
const WHEEL_STEP = 60 // pixels of scrolling per item
const WHEEL_PAUSE = 140 // ms between items, so a trackpad flick doesn't fly through the lot
const WHEEL_SETTLE = 450 // ms after the last item during which the flick's tail is swallowed
const MARGIN = 16 // room kept around the pile when scrolling it into view

/**
 * Things in a messy pile, the first on top (achievement cards, Rewind's records). The scroll
 * wheel first brings the whole pile into view, then flips through it, then lets the page (or
 * the box it's in) scroll on past the last one (or the first, going up). A swipe, a click,
 * the arrow keys and the buttons flip too. Taps and keys are kept to the pile, so a pile
 * inside something that reacts to them (Rewind's slides) doesn't move that along too.
 */
export function Pile<T>({
  items,
  itemKey,
  renderItem,
  label,
  className,
  messiness = 1,
  onTopChange,
  dark = false,
  noun = 'card',
}: {
  items: T[]
  itemKey: (item: T) => string
  renderItem: (item: T, onTop: boolean) => ReactNode
  label: string
  /** The pile's size (it needs a height: its items lie on top of each other). */
  className: string
  /** How untidy the pile is: 1 for cards thrown down, less for a neat stack. */
  messiness?: number
  onTopChange?: (index: number) => void
  /** On a dark background of its own (Rewind): light buttons. */
  dark?: boolean
  /** What one item is called, for the buttons ("Next card"). */
  noun?: string
}) {
  const [top, setTop] = useState(0)
  const ref = useRef<HTMLDivElement>(null)
  const topRef = useRef(0)
  const n = items.length
  // Kept current for the wheel handler (set up once).
  const changed = useRef(onTopChange)
  useEffect(() => {
    changed.current = onTopChange
  }, [onTopChange])
  const go = (to: number) => {
    const next = Math.max(0, Math.min(n - 1, to))
    topRef.current = next
    setTop(next)
    changed.current?.(next)
  }

  useEffect(() => {
    const el = ref.current
    if (!el || n < 2) return
    let pending = 0
    let last = -Infinity
    // Not React's onWheel: that one can't stop the page scrolling along.
    const onWheel = (e: WheelEvent) => {
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? window.innerHeight : 1
      const vertical = Math.abs(e.deltaY) >= Math.abs(e.deltaX)
      const delta = (vertical ? e.deltaY : e.deltaX) * unit
      if (!delta) return
      const down = delta > 0

      // First the page scrolls until the whole pile is in view (if it fits), and stops there.
      const box = el.getBoundingClientRect()
      const page = document.scrollingElement ?? document.documentElement
      const fits = box.height + 2 * MARGIN <= window.innerHeight
      const short = down
        ? Math.min(box.bottom + MARGIN - window.innerHeight, page.scrollHeight - page.clientHeight - page.scrollTop)
        : Math.min(MARGIN - box.top, page.scrollTop)
      if (vertical && fits && short > 0) {
        e.preventDefault()
        window.scrollBy({ top: down ? Math.min(delta, short) : Math.max(delta, -short) })
        pending = 0
        return
      }

      // Past the last item (or before the first): the page scrolls on, once this flick is over.
      if (down ? topRef.current >= n - 1 : topRef.current <= 0) {
        if (e.timeStamp - last < WHEEL_SETTLE) e.preventDefault()
        pending = 0
        return
      }
      e.preventDefault()
      pending += delta
      if (Math.abs(pending) < WHEEL_STEP || e.timeStamp - last < WHEEL_PAUSE) return
      topRef.current = Math.max(0, Math.min(n - 1, topRef.current + Math.sign(pending)))
      setTop(topRef.current)
      changed.current?.(topRef.current)
      pending = 0
      last = e.timeStamp
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [n])

  const swipe = useRef<number | null>(null)
  return (
    <div className="flex flex-col items-center gap-3" data-pile>
      <div
        ref={ref}
        role="group"
        aria-roledescription="pile"
        aria-label={label}
        tabIndex={0}
        className={`relative touch-pan-y rounded-2xl outline-offset-4 ${className}`}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight' || e.key === 'ArrowDown') go(top + 1)
          else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') go(top - 1)
          else return
          e.preventDefault()
          e.stopPropagation()
        }}
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => (swipe.current = e.clientX)}
        onPointerUp={(e) => {
          if (swipe.current === null) return
          const dx = e.clientX - swipe.current
          swipe.current = null
          if (Math.abs(dx) > 40) go(top + (dx < 0 ? 1 : -1))
          // A click on the last one gathers the pile up again.
          else if (n > 1) go(top + 1 < n ? top + 1 : 0)
        }}
      >
        {items.map((item, i) => {
          const depth = i - top < 0 ? i - top + n : i - top
          const shown = Math.min(depth, SHOWN - 1)
          const j = jitter(itemKey(item))
          // The top one lies nearly straight so it's easy to see; the ones under it less so.
          const tidy = (depth === 0 ? 0.2 : 1) * messiness
          return (
            <div
              key={itemKey(item)}
              aria-hidden={depth !== 0}
              className="absolute left-1/2 top-2 motion-safe:transition-[transform,opacity] motion-safe:duration-300 motion-safe:ease-out"
              style={{
                zIndex: n - depth,
                opacity: depth < SHOWN ? 1 : 0,
                transform: `translateX(-50%) translate(${j.x * tidy}px, ${j.y * tidy + shown * 3}px) rotate(${j.rotate * tidy}deg) scale(${1 - shown * 0.015})`,
              }}
            >
              {renderItem(item, depth === 0)}
            </div>
          )
        })}
      </div>
      {n > 1 && (
        <div className="flex items-center gap-3 text-sm">
          <Button
            variant={dark ? 'ghost' : 'secondary'}
            size="none"
            onClick={(e) => (e.stopPropagation(), go(top - 1))}
            disabled={top === 0}
            className={`px-3 py-1 text-sm ${dark ? 'text-white hover:bg-white/15' : ''}`}
            aria-label={`Previous ${noun}`}
          >
            ‹
          </Button>
          <span className={`tabular-nums ${dark ? 'text-white/80' : 'text-muted'}`} aria-live="polite">
            {top + 1} / {n}
          </span>
          <Button
            variant={dark ? 'ghost' : 'secondary'}
            size="none"
            onClick={(e) => (e.stopPropagation(), go(top + 1))}
            disabled={top === n - 1}
            className={`px-3 py-1 text-sm ${dark ? 'text-white hover:bg-white/15' : ''}`}
            aria-label={`Next ${noun}`}
          >
            ›
          </Button>
        </div>
      )}
    </div>
  )
}
