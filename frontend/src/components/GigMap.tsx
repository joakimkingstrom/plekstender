import 'maplibre-gl/dist/maplibre-gl.css'
import { LngLatBounds, Map as MapLibre, Marker, NavigationControl, Popup, setWorkerUrl } from 'maplibre-gl'
// MapLibre looks for its worker next to its own file, which bundling moves: say where it is.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { useEffect, useMemo, useRef } from 'react'
import Supercluster from 'supercluster'
import type { Gig } from '../api/gigs'
import { gigPlaces, mapStyle, pinCount, pinOffsets, placeName, siteColours, type GigPlace } from '../gigMap'
import { localDate } from '../live'

type Props = { placeId: string; gigs: number }

setWorkerUrl(workerUrl)

let pinId = 0

/** A pushpin with a round, shiny head (red; grey where only the town is known), a steel
 * needle and a soft shadow. */
function pushpin(tilt: number, grey = false): SVGSVGElement {
  const id = `pin${pinId++}`
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.setAttribute('viewBox', '0 0 24 40')
  svg.setAttribute('width', '22')
  svg.setAttribute('height', '37')
  svg.setAttribute('aria-hidden', 'true')
  svg.style.transformOrigin = '12px 39px'
  svg.style.transform = `rotate(${tilt}deg)`
  svg.innerHTML = `
    <defs>
      <radialGradient id="${id}h" cx="38%" cy="32%" r="70%">
        <stop offset="0" stop-color="${grey ? '#e2e4e7' : '#ff8a80'}"/>
        <stop offset="0.45" stop-color="${grey ? '#9a9ea5' : '#e3242b'}"/>
        <stop offset="1" stop-color="${grey ? '#4d5056' : '#7f0d12'}"/>
      </radialGradient>
      <linearGradient id="${id}n" x1="0" x2="1">
        <stop offset="0" stop-color="#8d9196"/>
        <stop offset="0.5" stop-color="#e4e6e8"/>
        <stop offset="1" stop-color="#6b6f74"/>
      </linearGradient>
      <filter id="${id}s" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="0.9"/></filter>
    </defs>
    <ellipse cx="13.5" cy="38.6" rx="4.2" ry="1.3" fill="#000" opacity="0.28" filter="url(#${id}s)"/>
    <rect x="11.1" y="16" width="1.8" height="23" rx="0.9" fill="url(#${id}n)"/>
    <circle cx="12" cy="10" r="8.6" fill="url(#${id}h)"/>
    <ellipse cx="9" cy="6.8" rx="2.8" ry="1.9" fill="#fff" opacity="0.75" transform="rotate(-28 9 6.8)"/>`
  return svg
}

function markerElement(gigs: number, title: string, onClick: () => void, grey = false): HTMLElement {
  const el = document.createElement('button')
  el.type = 'button'
  el.title = title
  el.setAttribute('aria-label', title)
  // No `position` here: MapLibre places markers absolutely (and the pins inside sit on that).
  el.style.cssText = 'width:40px;height:44px;background:none;border:0;padding:0;cursor:pointer'
  pinOffsets(pinCount(gigs)).forEach(({ x, y, tilt }) => {
    const pin = pushpin(tilt, grey)
    pin.style.position = 'absolute'
    pin.style.left = `${9 + x}px`
    pin.style.top = `${5 + y}px`
    el.appendChild(pin)
  })
  if (gigs > 3) {
    const badge = document.createElement('span')
    badge.textContent = String(gigs)
    badge.style.cssText =
      'position:absolute;right:-6px;top:-4px;min-width:18px;height:18px;padding:0 4px;border-radius:9px;' +
      'background:var(--color-text);color:var(--color-bg);font:600 11px/18px system-ui;text-align:center'
    el.appendChild(badge)
  }
  el.addEventListener('click', (e) => {
    e.stopPropagation()
    onClick()
  })
  return el
}

/** The gigs at a place, as plain text (names come from imports, so no HTML). Where the
 * gigs were at different venues (or only the town is known), each says its venue. */
function placeList(place: GigPlace): HTMLElement {
  const box = document.createElement('div')
  box.style.cssText = 'max-height:220px;overflow:auto;font:13px/1.4 system-ui;color:#1c1d21'
  const head = document.createElement('div')
  head.style.fontWeight = '600'
  head.textContent = placeName(place)
  box.appendChild(head)
  if (place.approximate) {
    const note = document.createElement('div')
    note.style.cssText = 'margin-bottom:4px;font-size:12px;color:#5f6168'
    note.textContent = 'These venues weren’t found on the map, so they’re shown at the town. Edit a gig to pick its place.'
    box.appendChild(note)
  }
  for (const gig of [...place.gigs].sort((a, b) => b.date.localeCompare(a.date))) {
    const row = document.createElement('div')
    const date = localDate(gig.date).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
    const artists = gig.artists
      .slice(0, 3)
      .map((a) => a.name)
      .join(', ')
    row.textContent = [date, artists, place.venue ? null : gig.venue].filter(Boolean).join(' · ')
    box.appendChild(row)
  }
  return box
}

export type MapPoint = { latitude: number; longitude: number }

/** Show these points: one up close, several so they all fit. */
function frame(map: MapLibre, points: MapPoint[], animate: boolean) {
  if (points.length === 0) return
  const duration = animate ? 800 : 0
  if (points.length === 1) {
    map.easeTo({ center: [points[0].longitude, points[0].latitude], zoom: 12, duration })
    return
  }
  const bounds = new LngLatBounds()
  for (const p of points) bounds.extend([p.longitude, p.latitude])
  map.fitBounds(bounds, { padding: 48, maxZoom: 12, duration })
}

/**
 * The gigs someone went to, as pushpins on a plain street map (zooms down to the venue).
 * `focus`: the places to frame (the gigs a filter picked out); null frames every gig, and
 * undefined leaves the map where it is.
 */
export default function GigMap({ gigs, focus }: { gigs: Gig[]; focus?: MapPoint[] | null }) {
  const container = useRef<HTMLDivElement>(null)
  const places = useMemo(() => gigPlaces(gigs), [gigs])
  const mapRef = useRef<MapLibre | null>(null)
  const focusRef = useRef<MapPoint[] | null>(null)
  // Only move when the places to frame change (not on every refetch).
  const focusKey = focus === undefined ? undefined : focus === null ? 'all' : focus.map((p) => `${p.latitude},${p.longitude}`).sort().join(';')

  useEffect(() => {
    const element = container.current
    if (!element || places.length === 0) return
    const map = new MapLibre({
      container: element,
      style: mapStyle(siteColours()),
      center: [places[0].longitude, places[0].latitude],
      zoom: 3,
      maxZoom: 18,
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    })
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.touchZoomRotate.disableRotation()

    const index = new Supercluster<Props, { gigs: number }>({
      radius: 48,
      maxZoom: 16,
      map: (p) => ({ gigs: p.gigs }),
      reduce: (acc, p) => {
        acc.gigs += p.gigs
      },
    })
    const byId = new Map(places.map((p) => [p.id, p]))
    index.load(
      places.map((p) => ({
        type: 'Feature' as const,
        geometry: { type: 'Point' as const, coordinates: [p.longitude, p.latitude] },
        properties: { placeId: p.id, gigs: p.gigs.length },
      })),
    )

    let markers: Marker[] = []
    let popup: Popup | null = null
    const draw = () => {
      for (const m of markers) m.remove()
      markers = []
      const b = map.getBounds()
      const zoom = Math.round(map.getZoom())
      for (const feature of index.getClusters([b.getWest() - 10, b.getSouth() - 10, b.getEast() + 10, b.getNorth() + 10], zoom)) {
        const [lng, lat] = feature.geometry.coordinates as [number, number]
        const props = feature.properties as Partial<Props> & { cluster?: boolean; cluster_id?: number; gigs: number }
        let el: HTMLElement
        if (props.cluster && props.cluster_id != null) {
          const clusterId = props.cluster_id
          el = markerElement(props.gigs, `${props.gigs} gigs here: zoom in`, () => {
            map.easeTo({ center: [lng, lat], zoom: Math.min(index.getClusterExpansionZoom(clusterId), 17) })
          })
        } else {
          const place = byId.get(props.placeId!)!
          const name = `${placeName(place)}${place.approximate ? ' (venues not found)' : ''}`
          el = markerElement(
            place.gigs.length,
            `${name}: ${place.gigs.length} ${place.gigs.length === 1 ? 'gig' : 'gigs'}`,
            () => {
              popup?.remove()
              popup = new Popup({ offset: 30, maxWidth: '300px' }).setLngLat([lng, lat]).setDOMContent(placeList(place)).addTo(map)
            },
            place.approximate,
          )
        }
        markers.push(new Marker({ element: el, anchor: 'bottom', offset: [0, 4] }).setLngLat([lng, lat]).addTo(map))
      }
    }
    map.on('load', () => {
      const chosen = focusRef.current
      frame(map, chosen?.length ? chosen : places, false)
      mapRef.current = map
      draw()
    })
    map.on('moveend', draw)

    // Follow the site's theme.
    const observer = new MutationObserver(() => map.setStyle(mapStyle(siteColours())))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    // Keep the map's idea of its size in step with its box (layout and styles settle after
    // it's made; the page reflows on phones): otherwise the pins drift off their places.
    const resized = new ResizeObserver(() => map.resize())
    resized.observe(element)
    return () => {
      resized.disconnect()
      observer.disconnect()
      for (const m of markers) m.remove()
      mapRef.current = null
      map.remove()
    }
  }, [places])

  // A filter picked: frame its gigs (back to every gig when the filters are cleared).
  useEffect(() => {
    if (focus === undefined) return
    focusRef.current = focus
    const map = mapRef.current
    if (!map) return
    const chosen = focus
    frame(map, chosen?.length ? chosen : places, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `focus` is new each render; its key isn't
  }, [focusKey, places])

  return (
    <div
      ref={container}
      className="h-[26rem] w-full overflow-hidden rounded-2xl border border-border"
      role="region"
      aria-label={`Map of ${places.length} ${places.length === 1 ? 'place' : 'places'} you went to gigs`}
    />
  )
}
