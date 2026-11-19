import { useQuery } from '@tanstack/react-query'
import { geoArea, geoNaturalEarth1, geoPath, type GeoPermissibleObjects } from 'd3-geo'
import type { Feature, FeatureCollection, Geometry, MultiPolygon, Polygon } from 'geojson'
import { useMemo, useState, type KeyboardEvent } from 'react'
import { feature } from 'topojson-client'
import type { GeometryCollection, Topology } from 'topojson-specification'
import worldUrl from '../assets/countries-110m.json?url'
import type { MapArea } from '../api/stats'
import { STEPS, countryName, step } from '../places'
import { formatCount } from '../format'

export type CountryListens = { country_code: string; continent_code: string | null; listens: number }
type Props = { a2: string; c: string; n: string }
type Country = Feature<Geometry, Props>

const WIDTH = 960
const HEIGHT = 500
// Left out when fitting a continent, so it isn't zoomed out by a far-flung corner
// (Russia's east for Europe, Fiji across the date line for Oceania).
const FIT_EXCLUDE: Record<string, string[]> = { EU: ['RU'], OC: ['FJ'], AS: ['RU'] }

/** For fitting the view: a country's main landmass(es), without far-off territories
 * (French Guiana for France, Svalbard for Norway) that would zoom the map right out. */
function mainland(f: Country, share: number): Country {
  if (f.geometry.type !== 'MultiPolygon') return f
  const parts = f.geometry.coordinates.map((coordinates) => {
    const polygon: Polygon = { type: 'Polygon', coordinates }
    return { coordinates, area: geoArea(polygon) }
  })
  const largest = Math.max(...parts.map((p) => p.area))
  const kept: MultiPolygon = {
    type: 'MultiPolygon',
    coordinates: parts.filter((p) => p.area >= largest * share).map((p) => p.coordinates),
  }
  return { ...f, geometry: kept }
}

function useWorld() {
  return useQuery({
    queryKey: ['world-map'],
    queryFn: async () => {
      const resp = await fetch(worldUrl)
      if (!resp.ok) throw new Error('The map could not be loaded.')
      const topo = (await resp.json()) as Topology<{ countries: GeometryCollection<Props> }>
      return (feature(topo, topo.objects.countries) as FeatureCollection<Geometry, Props>).features
    },
    staleTime: Infinity,
  })
}

function fill(s: number) {
  return s < 0
    ? 'var(--color-surface-2)'
    : `color-mix(in oklab, var(--color-chart) ${STEPS[s]}%, var(--color-surface-2))`
}

/**
 * A choropleth of listens per country. Click (or Enter on) a country to zoom to its
 * continent, then to the country itself. Countries without listens are drawn but inert.
 */
export function ListensMap({
  countries,
  area,
  onArea,
}: {
  countries: CountryListens[]
  area: MapArea
  onArea: (area: MapArea) => void
}) {
  const world = useWorld()
  const [hover, setHover] = useState<{ code: string; x: number; y: number } | null>(null)
  const counts = useMemo(() => new Map(countries.map((c) => [c.country_code, c.listens])), [countries])
  const max = Math.max(0, ...countries.map((c) => c.listens))

  const shapes = world.data
  const drawn = useMemo(() => {
    if (!shapes) return null
    const focus: Country[] =
      area.level === 'world'
        ? []
        : area.level === 'continent'
          ? shapes
              .filter((f) => f.properties.c === area.continent && !FIT_EXCLUDE[area.continent]?.includes(f.properties.a2))
              .map((f) => mainland(f, 1))
          : shapes.filter((f) => f.properties.a2 === area.country).map((f) => mainland(f, 0.3))
    const target: GeoPermissibleObjects =
      focus.length > 0 ? { type: 'FeatureCollection', features: focus } : { type: 'Sphere' }
    const pad = area.level === 'world' ? 4 : 24
    const projection = geoNaturalEarth1().fitExtent(
      [
        [pad, pad],
        [WIDTH - pad, HEIGHT - pad],
      ],
      target,
    )
    const path = geoPath(projection)
    return {
      sphere: path({ type: 'Sphere' }) ?? '',
      countries: shapes.map((f) => ({ f, d: path(f) ?? '' })),
    }
  }, [shapes, area])

  if (world.isPending) return <div className="aspect-[96/50] w-full animate-pulse rounded-xl bg-surface-2" />
  if (world.isError || !drawn) return <p className="text-sm text-danger">The map could not be loaded.</p>

  const inArea = (f: Country) =>
    area.level === 'world' ||
    (area.level === 'continent' ? f.properties.c === area.continent : f.properties.a2 === area.country)

  const choose = (f: Country) => {
    const code = f.properties.a2
    if (!counts.get(code)) return
    if (area.level === 'world') onArea({ level: 'continent', continent: f.properties.c })
    else if (area.level === 'continent' && f.properties.c === area.continent)
      onArea({ level: 'country', continent: area.continent, country: code })
    else onArea({ level: 'country', continent: f.properties.c, country: code })
  }
  const onKey = (e: KeyboardEvent, f: Country) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      choose(f)
    }
  }

  const hovered = hover ? { code: hover.code, listens: counts.get(hover.code) ?? 0 } : null
  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full select-none"
        role="group"
        aria-label="Listens by country"
        onMouseLeave={() => setHover(null)}
      >
        <path d={drawn.sphere} fill="var(--color-bg)" stroke="var(--color-border)" strokeWidth={1} />
        {drawn.countries.map(({ f, d }) => {
          const code = f.properties.a2
          const listens = counts.get(code) ?? 0
          const active = listens > 0
          const dim = !inArea(f)
          return (
            <path
              key={`${code}-${f.properties.n}`}
              d={d}
              fill={fill(step(listens, max))}
              stroke="var(--color-surface)"
              strokeWidth={0.6}
              opacity={dim ? 0.35 : 1}
              tabIndex={active && !dim ? 0 : undefined}
              role={active ? 'button' : undefined}
              aria-label={active ? `${countryName(code)}: ${formatCount(listens)} listens` : undefined}
              className={active ? 'cursor-pointer outline-none transition-opacity hover:brightness-110 focus-visible:stroke-[var(--color-text)] focus-visible:[stroke-width:2]' : ''}
              onClick={() => choose(f)}
              onKeyDown={(e) => onKey(e, f)}
              onMouseMove={(e) => {
                const box = e.currentTarget.ownerSVGElement?.getBoundingClientRect()
                if (box) setHover({ code, x: e.clientX - box.left, y: e.clientY - box.top })
              }}
            />
          )
        })}
      </svg>
      {hover && hovered && (
        <div
          role="tooltip"
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md border border-border bg-surface-2 px-2.5 py-1.5 text-xs shadow-lg"
          style={{ left: hover.x, top: hover.y - 10 }}
        >
          <span className="font-medium">{countryName(hovered.code)}</span>
          <span className="text-muted">
            {' · '}
            {hovered.listens ? `${formatCount(hovered.listens)} ${hovered.listens === 1 ? 'listen' : 'listens'}` : 'no listens'}
          </span>
        </div>
      )}
      <Legend max={max} />
    </div>
  )
}

function Legend({ max }: { max: number }) {
  if (max <= 0) return null
  // The smallest count in each step.
  const starts = STEPS.map((_, i) => (i === 0 ? 1 : Math.ceil(Math.exp((Math.log(max) * i) / STEPS.length))))
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted" aria-hidden>
      <span>Listens</span>
      <span className="flex items-center gap-1">
        <span className="size-3 rounded-sm border border-border" style={{ background: fill(-1) }} />
        none
      </span>
      {STEPS.map((_, i) => {
        if (i > 0 && starts[i] <= starts[i - 1]) return null
        return (
          <span key={i} className="flex items-center gap-1">
            <span className="size-3 rounded-sm" style={{ background: fill(i) }} />
            {formatCount(starts[i])}+
          </span>
        )
      })}
    </div>
  )
}
