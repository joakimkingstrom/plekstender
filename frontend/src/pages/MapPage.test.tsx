import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { step } from '../places'
import { MapPage } from './MapPage'

let fetchMock: ReturnType<typeof vi.fn<(input: Request | string) => Promise<Response>>>

beforeEach(() => {
  fetchMock = vi.fn<(input: Request | string) => Promise<Response>>(async (input) => {
    const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
    const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
    if (!url.pathname.startsWith('/api/'))
      // The map's shapes: an empty world is enough here.
      return json({ type: 'Topology', objects: { countries: { type: 'GeometryCollection', geometries: [] } }, arcs: [] })
    if (url.pathname === '/api/servers')
      return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
    if (url.pathname === '/api/listens/summary') return json({ total: 10, first_at: null, last_at: null, by_source: {} })
    if (url.pathname === '/api/stats/places')
      return json({
        countries: [
          { country_code: 'GB', continent_code: 'EU', listens: 7, listening_ms: 0 },
          { country_code: 'JP', continent_code: 'AS', listens: 2, listening_ms: 0 },
        ],
        located: 9,
        total: 12,
        attribution: 'IP geolocation by DB-IP',
      })
    if (url.pathname === '/api/stats/places/detail') {
      const country = url.searchParams.get('country')
      return json({
        listens: country ? 7 : 9,
        listening_ms: 0,
        // Linked to the library (for pictures and links) only when it's asked for.
        top_artists: [{ name: country ? 'Portishead' : 'Radiohead', artist: null, listens: 5, listening_ms: 0, previous_rank: null, rating_key: url.searchParams.get('server_id') === '1' ? 100 : null, thumb: null }],
        top_tracks: [],
        cities: country ? [{ name: 'Local network', listens: 7 }] : [],
      })
    }
    return json({ detail: 'Not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('MapPage', () => {
  it('lists countries and zooms into one', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/history/map']}>
          <Routes>
            <Route path="/history/map" element={<MapPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByText(/9 of 12 listens in this period have a place/)).toBeInTheDocument()
    expect((await screen.findByRole('link', { name: /Radiohead/ })).getAttribute('href')).toBe('/library/artists/100')
    expect(await screen.findByText('Radiohead')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /United Kingdom/ }))
    expect(await screen.findByRole('heading', { name: 'United Kingdom' })).toBeInTheDocument()
    expect(await screen.findByText('Portishead')).toBeInTheDocument()
    expect(screen.getByText('At home (local network)')).toBeInTheDocument()
    // The breadcrumb leads back out.
    fireEvent.click(screen.getByRole('button', { name: 'Europe' }))
    expect(await screen.findByRole('heading', { name: 'Europe' })).toBeInTheDocument()
  })

  it('colours on a log scale', () => {
    expect(step(0, 100)).toBe(-1)
    expect(step(1, 100)).toBe(0)
    expect(step(100, 100)).toBe(4)
    expect(step(10, 100)).toBe(2)
    expect(step(1, 1)).toBe(4)
  })
})
