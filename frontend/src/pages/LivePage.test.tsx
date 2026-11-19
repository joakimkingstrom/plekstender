import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { followCount } from '../live'
import { LivePage } from './LivePage'

const base = {
  place_name: null as string | null,
  latitude: null as number | null,
  longitude: null as number | null,
  radius_km: 100,
  top_percent: 10,
  listened_artists: 40,
  followed_artists: 4,
  max_artists: 150,
  refresh_status: null as string | null,
  refresh_error: null,
  refreshed_at: null as string | null,
  refresh_available_at: null,
  source_ready: true,
}
const placed = {
  ...base,
  place_name: 'Stockholm, Sweden',
  latitude: 59.33,
  longitude: 18.07,
  refresh_status: 'done',
  refreshed_at: '2026-11-19T05:20:00Z',
}
const concert = {
  id: 1,
  source: 'jambase',
  name: 'Radiohead at Avicii Arena',
  festival: false,
  start_date: '2027-03-04',
  start_time: '19:30',
  end_date: null,
  status: 'scheduled',
  venue: 'Avicii Arena',
  city: 'Stockholm',
  region: null,
  country: 'SE',
  distance_km: 4.1,
  ticket_url: 'https://www.ticketmaster.se/event/radiohead',
  seller: 'Ticketmaster',
  page_url: 'https://www.jambase.com/show/radiohead',
  artists: [{ name: 'Radiohead', headliner: true, server_id: 1, rating_key: 100 }],
  performers: [
    { name: 'Radiohead', headliner: true },
    { name: 'Support Act', headliner: false },
  ],
}
const festival = {
  ...concert,
  id: 2,
  name: 'Way Out West 2027',
  festival: true,
  start_date: '2027-08-12',
  start_time: null,
  end_date: '2027-08-14',
  status: 'postponed',
  ticket_url: null,
  artists: [{ name: 'Portishead', headliner: true, server_id: 1, rating_key: 101 }],
  performers: [{ name: 'Portishead', headliner: true }],
}

let settings: typeof base = base
let fetchMock: ReturnType<typeof vi.fn<(input: Request | string, init?: RequestInit) => Promise<Response>>>

beforeEach(() => {
  settings = base
  fetchMock = vi.fn<(input: Request | string, init?: RequestInit) => Promise<Response>>(async (input) => {
    const request = typeof input === 'string' ? null : input
    const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    switch (url.pathname) {
      case '/api/auth/me':
        return json({ id: 1, username: 'owner', email: null, thumb_url: null, is_admin: true })
      case '/api/servers':
        return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      case '/api/live/settings':
        if (request?.method === 'PUT') {
          const body = await request.clone().json()
          return json({ ...placed, ...body, refresh_status: 'queued', refreshed_at: null })
        }
        return json(settings)
      case '/api/live/places':
        return json([{ name: 'Stockholm, Stockholm County, Sweden', latitude: 59.33, longitude: 18.07, country: 'SE' }])
      case '/api/live/shows': {
        const artist = url.searchParams.get('artist')
        const shows = [concert, festival].filter((s) => !artist || s.artists.some((a) => a.name === artist))
        return json({ shows, artists: [{ name: 'Radiohead', shows: 1 }, { name: 'Portishead', shows: 1 }] })
      }
      default:
        return json({ detail: 'Not found' }, 404)
    }
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <LivePage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function requestsTo(path: string) {
  return fetchMock.mock.calls
    .map(([input]) => input)
    .filter((input): input is Request => typeof input !== 'string' && new URL(input.url).pathname === path)
}

describe('LivePage', () => {
  it('counts the artists to follow like the server', () => {
    expect([0, 1, 9, 25, 5000].map((n) => followCount(n, 10, 150))).toEqual([0, 1, 1, 3, 150])
  })

  it('sets up a place, a distance and the artists to follow', async () => {
    renderPage()
    expect(await screen.findByText('Your 4 most played of 40 artists.')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search for a city' }), { target: { value: 'Stock' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Stockholm, Stockholm County, Sweden' }))
    fireEvent.change(screen.getByRole('slider'), { target: { value: '25' } })
    expect(screen.getByText('Your 10 most played of 40 artists.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await vi.waitFor(async () => {
      const [put] = requestsTo('/api/live/settings').filter((r) => r.method === 'PUT')
      expect(await put.clone().json()).toEqual({
        place_name: 'Stockholm, Stockholm County, Sweden',
        latitude: 59.33,
        longitude: 18.07,
        radius_km: 100,
        top_percent: 25,
      })
    })
    expect(await screen.findByText('Looking up shows…', { selector: 'p[aria-live]' })).toBeInTheDocument()
  })

  it('lists upcoming shows with ticket links, the JamBase credit, and an artist filter', async () => {
    settings = placed
    renderPage()
    const row = (await screen.findByRole('link', { name: 'Radiohead' })).closest('li')!
    expect(screen.getByRole('link', { name: 'Radiohead' })).toHaveAttribute('href', '/library/artists/100')
    expect(within(row).getByText('with Support Act')).toBeInTheDocument()
    expect(within(row).getByText(/Avicii Arena · Stockholm · .* · 19:30/)).toBeInTheDocument()
    expect(within(row).getByText('4 km away')).toBeInTheDocument()
    const tickets = within(row).getByRole('link', { name: 'Tickets' })
    expect(tickets).toHaveAttribute('href', 'https://www.ticketmaster.se/event/radiohead')
    expect(tickets).toHaveAttribute('rel', 'nofollow noopener noreferrer')
    // No ticket link: the JamBase page instead; a postponed show says so.
    expect(screen.getByRole('link', { name: 'Details' })).toHaveAttribute('href', 'https://www.jambase.com/show/radiohead')
    expect(screen.getByText('Postponed')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Powered by JamBase' })).toHaveAttribute('href', 'https://www.jambase.com')

    fireEvent.click(screen.getByRole('button', { name: /^Artist/ }))
    fireEvent.click(screen.getByRole('option', { name: 'Portishead (1)' }))
    await vi.waitFor(() => expect(screen.queryByRole('link', { name: 'Radiohead' })).not.toBeInTheDocument())
    expect(requestsTo('/api/live/shows').at(-1)!.url).toContain('artist=Portishead')
  })

  it('says when JamBase has no key yet', async () => {
    settings = { ...placed, source_ready: false }
    renderPage()
    expect(await screen.findByText(/Live needs a JamBase API key/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeDisabled()
  })
})
