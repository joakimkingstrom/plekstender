import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { formatCount } from '../format'
import { layoutCloud, type CloudWord, type Measure } from '../wordCloud'

/** Text widths from a canvas, in the display font; a rough guess where there's no canvas. */
function canvasMeasure(): Measure {
  const ctx = typeof document !== 'undefined' ? document.createElement('canvas').getContext?.('2d') : null
  const family =
    (typeof document !== 'undefined' && getComputedStyle(document.documentElement).getPropertyValue('--font-display').trim()) ||
    'sans-serif'
  return (text, size, weight) => {
    if (!ctx) return text.length * size * 0.58
    ctx.font = `${weight} ${size}px ${family}`
    return ctx.measureText(text).width
  }
}

const TIER_CLASS = ['text-muted', 'text-text', 'text-text'] as const

/** A word cloud sized to its container. Hover a word for how many names it's in. */
export function WordCloud({ words, label }: { words: CloudWord[]; label: string }) {
  const ref = useRef<HTMLUListElement>(null)
  const [width, setWidth] = useState(0)
  const [measure, setMeasure] = useState<Measure>(canvasMeasure)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setWidth(el.clientWidth)
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  // Measured before the display font has loaded, words come out the wrong width: redo it once it has.
  useEffect(() => {
    let live = true
    document.fonts?.ready.then(() => live && setMeasure(() => canvasMeasure()))
    return () => {
      live = false
    }
  }, [])

  const { placed, height } = useMemo(() => layoutCloud(words, width || 600, measure), [words, width, measure])

  return (
    <ul ref={ref} className="relative w-full overflow-hidden" style={{ height }} aria-label={label}>
      {placed.map((p) => (
        <li
          key={p.word}
          title={`${p.word}: in ${formatCount(p.count)} ${p.count === 1 ? 'name' : 'names'}`}
          className={`absolute whitespace-nowrap font-display leading-none motion-safe:transition-[left,top,font-size] motion-safe:duration-500 ${TIER_CLASS[p.tier]}`}
          style={{
            left: p.x,
            top: p.y,
            fontSize: p.size,
            fontWeight: p.weight,
            transform: `translate(-50%, -50%)${p.vertical ? ' rotate(-90deg)' : ''}`,
          }}
        >
          {p.word}
          <span className="sr-only">: {formatCount(p.count)}</span>
        </li>
      ))}
    </ul>
  )
}
