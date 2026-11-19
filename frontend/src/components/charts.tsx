import { useEffect, useRef, useState, type ReactNode } from 'react'
import { formatCompact, formatCount } from '../format'

export type ColumnPoint = {
  /** Full label for the tooltip and table, e.g. "Mon 16 Nov". */
  label: string
  /** Short axis label, e.g. "16 Nov". */
  axis: string
  value: number
}

const AXIS_W = 40
const AXIS_H = 22
const TOP = 8
const BAR_MAX = 24
const GAP = 2

/** Axis step for two clean steps above zero: max 105 → step 60 (axis 0 / 60 / 120). */
function niceStep(max: number): number {
  const raw = Math.max(1, max) / 2
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 7.5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw
  return Math.max(1, Math.ceil(step))
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    setWidth(el.clientWidth)
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

/** Column path with a 4px rounded top and a square base. */
function column(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h)
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
}

/** Single-series column chart (one hue, no legend: the card title names it). */
export function ColumnChart({
  points,
  unit,
  height = 180,
  caption,
}: {
  points: ColumnPoint[]
  unit: (n: number) => string
  height?: number
  caption: string
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(0, ...points.map((p) => p.value))
  const step = niceStep(max)
  const top = step * 2
  const plotW = Math.max(0, width - AXIS_W)
  const plotH = height - AXIS_H - TOP
  const band = points.length ? plotW / points.length : 0
  const barW = Math.max(1, Math.min(BAR_MAX, band - GAP))
  const y = (v: number) => TOP + plotH - (v / top) * plotH
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor(plotW / 64))))
  const hovered = hover !== null ? points[hover] : null

  return (
    <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={caption} className="block">
          {[0, step, top].map((tick) => (
            <g key={tick}>
              <line x1={AXIS_W} x2={width} y1={y(tick)} y2={y(tick)} stroke="var(--color-border)" strokeWidth={1} />
              <text x={AXIS_W - 8} y={y(tick)} dy="0.32em" textAnchor="end" className="fill-muted text-[11px] tabular-nums">
                {tick >= 10_000 ? formatCompact(tick) : formatCount(tick)}
              </text>
            </g>
          ))}
          {points.map((p, i) => {
            const x = AXIS_W + i * band + (band - barW) / 2
            const h = y(0) - y(p.value)
            return (
              <g key={i}>
                {p.value > 0 && (
                  <path
                    d={column(x, y(p.value), barW, h)}
                    fill="var(--color-chart)"
                    opacity={hover === null || hover === i ? 1 : 0.45}
                  />
                )}
                {/* Hit target: the whole band, taller than the bar. */}
                <rect
                  x={AXIS_W + i * band}
                  y={TOP}
                  width={band}
                  height={plotH}
                  fill="transparent"
                  onMouseEnter={() => setHover(i)}
                  onTouchStart={() => setHover(i)}
                />
                {i % labelEvery === 0 && (
                  <text x={AXIS_W + i * band + band / 2} y={height - 6} textAnchor="middle" className="fill-muted text-[11px]">
                    {p.axis}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
      )}
      {hovered && hover !== null && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg border border-border bg-surface-2 px-2.5 py-1.5 text-xs shadow-lg"
          style={{
            left: Math.min(Math.max(AXIS_W + hover * band + band / 2, 60), width - 60),
            top: Math.max(y(hovered.value) - 6, 40),
          }}
        >
          <div className="text-muted">{hovered.label}</div>
          <div className="font-medium tabular-nums">{unit(hovered.value)}</div>
        </div>
      )}
      <table className="sr-only">
        <caption>{caption}</caption>
        <tbody>
          {points.map((p, i) => (
            <tr key={i}>
              <th scope="row">{p.label}</th>
              <td>{unit(p.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const LEVELS = [22, 40, 58, 78, 100]

function levelColor(level: number): string {
  return `color-mix(in oklab, var(--color-chart) ${LEVELS[level]}%, var(--color-surface))`
}

function cellColor(value: number, max: number): string {
  if (value <= 0 || max <= 0) return 'var(--color-surface-2)'
  return levelColor(Math.min(LEVELS.length - 1, Math.floor((value / max) * LEVELS.length)))
}

/** Weekday x hour grid (rows Monday first), one hue from faint to full. */
export function Heatmap({
  grid,
  dayNames,
  unit,
  caption,
}: {
  grid: number[][]
  dayNames: string[]
  unit: (n: number) => string
  caption: string
}) {
  const [hover, setHover] = useState<[number, number] | null>(null)
  const max = Math.max(0, ...grid.flat())
  const hours = Array.from({ length: 24 }, (_, h) => h)
  const readout: ReactNode = hover
    ? `${dayNames[hover[0]]} ${String(hover[1]).padStart(2, '0')}:00–${String((hover[1] + 1) % 24).padStart(2, '0')}:00 · ${unit(grid[hover[0]][hover[1]])}`
    : 'Hover a square for details.'

  return (
    <div>
      <div
        className="grid gap-[2px]"
        style={{ gridTemplateColumns: '2.5rem repeat(24, minmax(0, 1fr))' }}
        role="img"
        aria-label={caption}
        onMouseLeave={() => setHover(null)}
      >
        <span />
        {hours.map((h) => (
          <span key={h} className="text-center text-[10px] text-muted">
            {h % 6 === 0 ? String(h).padStart(2, '0') : ''}
          </span>
        ))}
        {grid.map((row, d) => (
          <div key={d} className="contents">
            <span className="self-center pr-1 text-[11px] text-muted">{dayNames[d]}</span>
            {row.map((value, h) => (
              <span
                key={h}
                className="h-6 rounded-[3px] sm:h-7"
                style={{
                  background: cellColor(value, max),
                  outline: hover?.[0] === d && hover?.[1] === h ? '2px solid var(--color-text)' : undefined,
                }}
                onMouseEnter={() => setHover([d, h])}
                onTouchStart={() => setHover([d, h])}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span aria-live="polite">{readout}</span>
        <span className="flex items-center gap-1" aria-hidden>
          Less
          {[-1, ...LEVELS.keys()].map((level) => (
            <span
              key={level}
              className="size-3 rounded-[3px]"
              style={{ background: level < 0 ? 'var(--color-surface-2)' : levelColor(level) }}
            />
          ))}
          More
        </span>
      </div>
      <table className="sr-only">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            {hours.map((h) => (
              <th key={h} scope="col">{`${h}:00`}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, d) => (
            <tr key={d}>
              <th scope="row">{dayNames[d]}</th>
              {row.map((value, h) => (
                <td key={h}>{value}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
