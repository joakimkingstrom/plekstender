import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
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

/**
 * The element's width, kept up to date. Charts draw at this width, laid over a box of their
 * own height (absolute), so the drawing never makes the box (or the page) wider: a too-wide
 * first measurement on a phone would otherwise stick.
 */
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
  onSelect,
  labelSpacing = 64,
}: {
  points: ColumnPoint[]
  unit: (n: number) => string
  height?: number
  caption: string
  /** Makes bars clickable (and reachable with the arrow keys). */
  onSelect?: (index: number) => void
  /** Room each axis label needs (px); short labels (a month's letter) can go under every bar. */
  labelSpacing?: number
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const [focused, setFocused] = useState<number | null>(null)
  const bars = useRef<(SVGRectElement | null)[]>([])
  const tabbable = focused ?? points.length - 1
  const moveFocus = (index: number) => {
    const next = Math.max(0, Math.min(points.length - 1, index))
    setFocused(next)
    setHover(next)
    bars.current[next]?.focus()
  }
  const max = Math.max(0, ...points.map((p) => p.value))
  const step = niceStep(max)
  const top = step * 2
  const plotW = Math.max(0, width - AXIS_W)
  const plotH = height - AXIS_H - TOP
  const band = points.length ? plotW / points.length : 0
  const barW = Math.max(1, Math.min(BAR_MAX, band - GAP))
  const y = (v: number) => TOP + plotH - (v / top) * plotH
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor(plotW / labelSpacing))))
  const hovered = hover !== null ? points[hover] : null

  return (
    <div ref={ref} className="relative w-full" style={{ height }} onMouseLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={caption} className="absolute inset-0 block">
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
                {onSelect && focused === i && (
                  <rect x={AXIS_W + i * band + 1} y={TOP} width={band - 2} height={plotH} fill="none" stroke="var(--color-text)" strokeWidth={1.5} rx={3} pointerEvents="none" />
                )}
                {/* Hit target: the whole band, taller than the bar. */}
                <rect
                  ref={(el) => {
                    bars.current[i] = el
                  }}
                  x={AXIS_W + i * band}
                  y={TOP}
                  width={band}
                  height={plotH}
                  fill="transparent"
                  onMouseEnter={() => setHover(i)}
                  onTouchStart={() => setHover(i)}
                  {...(onSelect
                    ? {
                        role: 'button',
                        tabIndex: i === tabbable ? 0 : -1,
                        'aria-label': `${p.label}: ${unit(p.value)}`,
                        className: 'cursor-pointer focus:outline-none',
                        onClick: () => onSelect(i),
                        onFocus: () => {
                          setFocused(i)
                          setHover(i)
                        },
                        onKeyDown: (e: KeyboardEvent<SVGRectElement>) => {
                          if (e.key === 'ArrowRight') moveFocus(i + 1)
                          else if (e.key === 'ArrowLeft') moveFocus(i - 1)
                          else if (e.key === 'Home') moveFocus(0)
                          else if (e.key === 'End') moveFocus(points.length - 1)
                          else if (e.key === 'Enter' || e.key === ' ') onSelect(i)
                          else return
                          e.preventDefault()
                        },
                      }
                    : {})}
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
          {onSelect && hovered.value > 0 && <div className="text-muted">Click to see them</div>}
        </div>
      )}
      {/* In a box: a table ignores .sr-only's 1px width and would widen the page. */}
      <div className="sr-only">
        <table>
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
  onSelect,
}: {
  grid: number[][]
  dayNames: string[]
  unit: (n: number) => string
  caption: string
  /** Makes squares clickable (and reachable with the arrow keys). */
  onSelect?: (day: number, hour: number) => void
}) {
  const [hover, setHover] = useState<[number, number] | null>(null)
  const [focused, setFocused] = useState<[number, number]>([0, 0])
  const cells = useRef<Record<string, HTMLElement | null>>({})
  const moveFocus = (day: number, hour: number) => {
    const next: [number, number] = [Math.max(0, Math.min(6, day)), Math.max(0, Math.min(23, hour))]
    setFocused(next)
    setHover(next)
    cells.current[`${next[0]}-${next[1]}`]?.focus()
  }
  const max = Math.max(0, ...grid.flat())
  const hours = Array.from({ length: 24 }, (_, h) => h)
  const readout: ReactNode = hover
    ? `${dayNames[hover[0]]} ${String(hover[1]).padStart(2, '0')}:00–${String((hover[1] + 1) % 24).padStart(2, '0')}:00 · ${unit(grid[hover[0]][hover[1]])}`
    : onSelect
      ? 'Hover a square for details, click it to see those listens.'
      : 'Hover a square for details.'

  return (
    <div>
      <div
        className="grid gap-[2px]"
        style={{ gridTemplateColumns: '2.5rem repeat(24, minmax(0, 1fr))' }}
        role={onSelect ? 'group' : 'img'}
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
            {row.map((value, h) => {
              const style = {
                background: cellColor(value, max),
                outline: hover?.[0] === d && hover?.[1] === h ? '2px solid var(--color-text)' : undefined,
              }
              if (!onSelect) {
                return (
                  <span
                    key={h}
                    className="h-6 rounded-[3px] sm:h-7"
                    style={style}
                    onMouseEnter={() => setHover([d, h])}
                    onTouchStart={() => setHover([d, h])}
                  />
                )
              }
              return (
                <button
                  key={h}
                  type="button"
                  ref={(el) => {
                    cells.current[`${d}-${h}`] = el
                  }}
                  tabIndex={focused[0] === d && focused[1] === h ? 0 : -1}
                  aria-label={`${dayNames[d]} ${String(h).padStart(2, '0')}:00: ${unit(value)}`}
                  className="h-6 rounded-[3px] focus:outline-none sm:h-7"
                  style={style}
                  onMouseEnter={() => setHover([d, h])}
                  onFocus={() => {
                    setFocused([d, h])
                    setHover([d, h])
                  }}
                  onClick={() => onSelect(d, h)}
                  onKeyDown={(e) => {
                    if (e.key === 'ArrowRight') moveFocus(d, h + 1)
                    else if (e.key === 'ArrowLeft') moveFocus(d, h - 1)
                    else if (e.key === 'ArrowDown') moveFocus(d + 1, h)
                    else if (e.key === 'ArrowUp') moveFocus(d - 1, h)
                    else return
                    e.preventDefault()
                  }}
                />
              )
            })}
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
      <div className="sr-only">
      <table>
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
    </div>
  )
}

export type ShareRow = { name: string; shares: number[] }

/**
 * Rows (genres, decades) by columns (years), one hue from faint to full: each square is
 * that row's share of the column. Hover or touch a square for its value.
 */
export function ShareGrid({
  rows,
  columns,
  caption,
  rowHeader,
  note,
}: {
  rows: ShareRow[]
  columns: string[]
  caption: string
  /** What the rows are, for the table view ("Genre"). */
  rowHeader: string
  /** More about a column for the readout, e.g. "of 1,204 listens with a genre". */
  note?: (column: number) => string
}) {
  const [hover, setHover] = useState<[number, number] | null>(null)
  const max = Math.max(0, ...rows.flatMap((r) => r.shares))
  const percent = (share: number) => `${Math.round(share * 100)}%`
  // About six year labels, always including the latest.
  const every = Math.max(1, Math.ceil(columns.length / 6))
  const labelled = (i: number) => (columns.length - 1 - i) % every === 0
  const readout = hover
    ? `${rows[hover[0]].name} · ${columns[hover[1]]}: ${percent(rows[hover[0]].shares[hover[1]])}${note ? ` ${note(hover[1])}` : ''}`
    : 'Hover a square for details.'
  return (
    <div>
      <div
        className="grid gap-[2px]"
        style={{ gridTemplateColumns: `minmax(4.5rem, 9rem) repeat(${columns.length}, minmax(0, 1fr))` }}
        role="img"
        aria-label={caption}
        onMouseLeave={() => setHover(null)}
      >
        {rows.map((row, r) => (
          <div key={row.name} className="contents">
            <span className="truncate self-center pr-2 text-[11px] text-muted" title={row.name}>
              {row.name}
            </span>
            {row.shares.map((share, c) => (
              <span
                key={c}
                className="h-6 rounded-[3px] sm:h-7"
                style={{
                  background: cellColor(share, max),
                  outline: hover?.[0] === r && hover?.[1] === c ? '2px solid var(--color-text)' : undefined,
                }}
                onMouseEnter={() => setHover([r, c])}
                onTouchStart={() => setHover([r, c])}
              />
            ))}
          </div>
        ))}
        <span />
        {columns.map((label, c) => (
          <span key={c} className="text-center text-[10px] whitespace-nowrap text-muted">
            {labelled(c) ? label : ''}
          </span>
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
      <div className="sr-only">
        <table>
          <caption>{caption}</caption>
          <thead>
            <tr>
              <th scope="col">{rowHeader}</th>
              {columns.map((label, c) => (
                <th key={c} scope="col">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}>
                <th scope="row">{row.name}</th>
                {row.shares.map((share, c) => (
                  <td key={c}>{percent(share)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Single-series line over a soft area, for running totals (one hue, no legend: the card
 * title names it). Hover or touch shows a crosshair and the value. */
export function AreaChart({
  points,
  unit,
  height = 200,
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
  const plotW = Math.max(0, width - AXIS_W - 8)
  const plotH = height - AXIS_H - TOP
  const x = (i: number) => AXIS_W + (points.length > 1 ? (i / (points.length - 1)) * plotW : plotW / 2)
  const y = (v: number) => TOP + plotH - (v / top) * plotH
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join('')
  const area = points.length ? `${line}L${x(points.length - 1).toFixed(1)},${y(0)}L${x(0).toFixed(1)},${y(0)}Z` : ''
  // Axis labels where the label text changes (years), spaced at least ~56px apart.
  const ticks: number[] = []
  points.forEach((p, i) => {
    if ((i === 0 || p.axis !== points[i - 1].axis) && (ticks.length === 0 || x(i) - x(ticks.at(-1)!) >= 56)) ticks.push(i)
  })
  const pick = (clientX: number, box: DOMRect) => {
    if (points.length === 0 || plotW <= 0) return
    const t = (clientX - box.left - AXIS_W) / plotW
    setHover(Math.max(0, Math.min(points.length - 1, Math.round(t * (points.length - 1)))))
  }
  const hovered = hover !== null ? points[hover] : null

  return (
    <div ref={ref} className="relative w-full" style={{ height }} onMouseLeave={() => setHover(null)}>
      {width > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={caption}
          className="absolute inset-0 block touch-pan-y"
          onMouseMove={(e) => pick(e.clientX, e.currentTarget.getBoundingClientRect())}
          onTouchMove={(e) => pick(e.touches[0].clientX, e.currentTarget.getBoundingClientRect())}
          onTouchStart={(e) => pick(e.touches[0].clientX, e.currentTarget.getBoundingClientRect())}
        >
          {[0, step, top].map((tick) => (
            <g key={tick}>
              <line x1={AXIS_W} x2={width} y1={y(tick)} y2={y(tick)} stroke="var(--color-border)" strokeWidth={1} />
              <text x={AXIS_W - 8} y={y(tick)} dy="0.32em" textAnchor="end" className="fill-muted text-[11px] tabular-nums">
                {tick >= 10_000 ? formatCompact(tick) : formatCount(tick)}
              </text>
            </g>
          ))}
          <path d={area} fill="var(--color-chart)" opacity={0.15} />
          <path d={line} fill="none" stroke="var(--color-chart)" strokeWidth={2} strokeLinejoin="round" />
          {ticks.map((i) => (
            <text key={i} x={x(i)} y={height - 6} textAnchor="middle" className="fill-muted text-[11px]">
              {points[i].axis}
            </text>
          ))}
          {hovered && hover !== null && (
            <g pointerEvents="none">
              <line x1={x(hover)} x2={x(hover)} y1={TOP} y2={y(0)} stroke="var(--color-muted)" strokeWidth={1} strokeDasharray="3 3" />
              <circle cx={x(hover)} cy={y(hovered.value)} r={4} fill="var(--color-chart)" stroke="var(--color-surface)" strokeWidth={2} />
            </g>
          )}
        </svg>
      )}
      {hovered && hover !== null && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg border border-border bg-surface-2 px-2.5 py-1.5 text-xs shadow-lg"
          style={{ left: Math.min(Math.max(x(hover), 60), width - 60), top: Math.max(y(hovered.value) - 10, 40) }}
        >
          <div className="text-muted">{hovered.label}</div>
          <div className="font-medium tabular-nums">{unit(hovered.value)}</div>
        </div>
      )}
      <div className="sr-only">
      <table>
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
    </div>
  )
}
