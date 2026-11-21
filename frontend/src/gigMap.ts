import type { StyleSpecification } from 'maplibre-gl'
import type { Gig } from './api/gigs'

/**
 * A spot on the gig map: a venue, or a town for gigs whose venue wasn't found there
 * (`approximate`: shown at the town, each gig with its own venue).
 */
export type GigPlace = {
  id: string
  /** The venue when all its gigs share one; else null (each gig says its own). */
  venue: string | null
  city: string | null
  approximate: boolean
  latitude: number
  longitude: number
  gigs: Gig[]
}

const fold = (s: string | null) => (s ?? '').normalize('NFKD').replace(/\p{M}/gu, '').trim().toLowerCase()

/** Gigs with a place, one entry per spot, most first. Gigs only placed at their town are
 * kept apart from venues that happen to be found at the same point. */
export function gigPlaces(gigs: Gig[]): GigPlace[] {
  const places = new Map<string, GigPlace>()
  for (const gig of gigs) {
    if (gig.latitude == null || gig.longitude == null) continue
    const approximate = gig.place_precision === 'city'
    const id = `${approximate ? 'town:' : ''}${gig.latitude.toFixed(4)},${gig.longitude.toFixed(4)}`
    const place = places.get(id)
    if (place) {
      place.gigs.push(gig)
      if (place.venue !== null && fold(place.venue) !== fold(gig.venue)) place.venue = null
    } else
      places.set(id, {
        id,
        venue: approximate ? null : gig.venue,
        city: gig.city,
        approximate,
        latitude: gig.latitude,
        longitude: gig.longitude,
        gigs: [gig],
      })
  }
  return [...places.values()].sort((a, b) => b.gigs.length - a.gigs.length)
}

/** A spot's name: its venue and town, or just the town when its gigs were at several. */
export function placeName(place: GigPlace): string {
  return [place.venue, place.city].filter(Boolean).join(', ') || 'Somewhere'
}

/** How many pins a spot gets: one per gig, but never more than three. */
export const MAX_PINS = 3
export function pinCount(gigs: number): number {
  return Math.max(1, Math.min(MAX_PINS, gigs))
}

/** Where each of a spot's pins stands (px from the spot), fanned out a little. */
export function pinOffsets(pins: number): { x: number; y: number; tilt: number }[] {
  if (pins === 1) return [{ x: 0, y: 0, tilt: 0 }]
  if (pins === 2)
    return [
      { x: -6, y: 1, tilt: -10 },
      { x: 6, y: 0, tilt: 10 },
    ]
  return [
    { x: -9, y: 2, tilt: -14 },
    { x: 9, y: 2, tilt: 14 },
    { x: 0, y: -3, tilt: 0 },
  ]
}

export type MapColours = { bg: string; water: string; road: string; major: string; border: string; label: string; halo: string }

/** The site's colours, read from its CSS (they change with the theme). */
export function siteColours(): MapColours {
  const css = getComputedStyle(document.documentElement)
  const get = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback
  const light = document.documentElement.dataset.theme === 'light'
  return {
    bg: get('--color-surface-2', light ? '#efece5' : '#292a30'),
    water: light ? '#c9d6dd' : '#1c242c',
    road: get('--color-surface', light ? '#ffffff' : '#1f2025'),
    major: light ? '#d9d2c3' : '#3b3d44',
    border: get('--color-border', light ? '#dedad1' : '#34353c'),
    label: get('--color-muted', light ? '#66645e' : '#a19f99'),
    halo: get('--color-surface-2', light ? '#efece5' : '#292a30'),
  }
}

const TILES = 'https://tiles.openfreemap.org/planet'
const GLYPHS = 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf'

/** A plain street map in a few colours: land, water, roads, borders, place names. */
export function mapStyle(c: MapColours): StyleSpecification {
  const line = (id: string, classes: string[], minzoom: number, colour: string, widths: [number, number][]) => ({
    id,
    type: 'line' as const,
    source: 'osm',
    'source-layer': 'transportation',
    minzoom,
    filter: ['in', ['get', 'class'], ['literal', classes]] as unknown as never,
    layout: { 'line-cap': 'round' as const, 'line-join': 'round' as const },
    paint: {
      'line-color': colour,
      'line-width': ['interpolate', ['exponential', 1.5], ['zoom'], ...widths.flat()] as unknown as never,
    },
  })
  const label = (id: string, classes: string[], minzoom: number, maxzoom: number, size: [number, number][], bold = false) => ({
    id,
    type: 'symbol' as const,
    source: 'osm',
    'source-layer': 'place',
    minzoom,
    maxzoom,
    filter: ['in', ['get', 'class'], ['literal', classes]] as unknown as never,
    layout: {
      'text-field': ['coalesce', ['get', 'name:en'], ['get', 'name']] as unknown as never,
      'text-font': [bold ? 'Noto Sans Bold' : 'Noto Sans Regular'],
      'text-size': ['interpolate', ['linear'], ['zoom'], ...size.flat()] as unknown as never,
      'text-max-width': 8,
    },
    paint: { 'text-color': c.label, 'text-halo-color': c.halo, 'text-halo-width': 1.2 },
  })
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: { osm: { type: 'vector', url: TILES, attribution: '© OpenMapTiles © OpenStreetMap contributors' } },
    layers: [
      { id: 'land', type: 'background', paint: { 'background-color': c.bg } },
      { id: 'water', type: 'fill', source: 'osm', 'source-layer': 'water', paint: { 'fill-color': c.water } },
      {
        id: 'countries',
        type: 'line',
        source: 'osm',
        'source-layer': 'boundary',
        filter: ['all', ['==', ['get', 'admin_level'], 2], ['!=', ['get', 'maritime'], 1]] as unknown as never,
        paint: { 'line-color': c.border, 'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.6, 8, 1.4] as unknown as never },
      },
      line('streets', ['minor', 'service', 'tertiary'], 12, c.road, [[12, 0.5], [18, 10]]),
      line('roads', ['primary', 'secondary', 'trunk'], 7, c.road, [[7, 0.4], [18, 16]]),
      line('motorways', ['motorway'], 5, c.major, [[5, 0.5], [18, 18]]),
      {
        id: 'street-names',
        type: 'symbol',
        source: 'osm',
        'source-layer': 'transportation_name',
        minzoom: 14,
        layout: {
          'symbol-placement': 'line',
          'text-field': ['coalesce', ['get', 'name:en'], ['get', 'name']] as unknown as never,
          'text-font': ['Noto Sans Regular'],
          'text-size': 11,
        },
        paint: { 'text-color': c.label, 'text-halo-color': c.road, 'text-halo-width': 1 },
      },
      label('neighbourhoods', ['suburb', 'neighbourhood', 'quarter'], 12, 24, [[12, 11], [16, 13]]),
      label('towns', ['town', 'village'], 8, 24, [[8, 11], [14, 14]]),
      label('cities', ['city'], 3, 24, [[3, 11], [10, 16]], true),
      label('country-names', ['country'], 1, 6, [[1, 10], [5, 13]]),
    ],
  }
}
