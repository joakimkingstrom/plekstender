import { describe, expect, it } from 'vitest'
import type { Gig } from './api/gigs'
import { gigPlaces, mapStyle, pinCount, pinOffsets } from './gigMap'

const gig = (id: number, venue: string, lat: number | null, lon: number | null): Gig => ({
  id, date: `2020-01-0${id}`, name: null, venue, city: 'London', country: 'GB', latitude: lat, longitude: lon,
  place_precision: lat == null ? null : 'venue', notes: null, artists: [{ name: 'X', server_id: null, rating_key: null }],
  source: 'manual', url: null,
})  // prettier-ignore

describe('gig map', () => {
  it('puts gigs at the same venue in one place, most first, and leaves out unplaced ones', () => {
    const places = gigPlaces([
      gig(1, 'Brixton Academy', 51.4651, -0.1149),
      gig(2, 'Royal Albert Hall', 51.50101, -0.17741),
      gig(3, 'Royal Albert Hall', 51.501012, -0.177409),
      gig(4, 'Nowhere', null, null),
    ])
    expect(places.map((p) => [p.venue, p.gigs.length])).toEqual([
      ['Royal Albert Hall', 2],
      ['Brixton Academy', 1],
    ])
  })

  it('gives a pin per gig, three at most, fanned out', () => {
    expect([1, 2, 3, 4, 250].map(pinCount)).toEqual([1, 2, 3, 3, 3])
    expect(pinOffsets(1)).toEqual([{ x: 0, y: 0, tilt: 0 }])
    expect(pinOffsets(3).map((o) => o.tilt)).toEqual([-14, 14, 0])
  })

  it('builds a plain street map from a few colours', () => {
    const colours = { bg: '#111', water: '#123', road: '#222', major: '#333', border: '#444', label: '#999', halo: '#111' }
    const style = mapStyle(colours)
    expect(style.sources.osm).toMatchObject({ type: 'vector', url: 'https://tiles.openfreemap.org/planet' })
    expect(style.layers.map((l) => l.id)).toEqual([
      'land', 'water', 'countries', 'streets', 'roads', 'motorways', 'street-names', 'neighbourhoods', 'towns', 'cities', 'country-names',
    ])  // prettier-ignore
    const used = new Set(JSON.stringify(style.layers).match(/#[0-9a-f]{3}\b/g))
    expect(used.size).toBeLessThanOrEqual(7)
  })
})
