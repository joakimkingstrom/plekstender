import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ArtistPage } from './LibraryPage'

const gig = {
  id: 7,
  ids: [7],
  source: 'jambase',
  sources: ['jambase'],
  starred: false,
  got_ticket: false,
  first_seen_at: null,
  is_new: false,
  tickets: [{ source: 'jambase', url: 'https://tickets.example/rh', seller: 'Ticketmaster', is_ticket: true }],
  name: 'Radiohead at the O2',
  festival: false,
  start_date: '2027-03-04',
  start_time: '19:30',
  end_date: null,
  status: 'scheduled',
  venue: 'The O2',
  city: 'London',
  region: null,
  country: 'GB',
  distance_km: 12.3,
  ticket_url: 'https://tickets.example/rh',
  seller: 'Ticketmaster',
  page_url: null,
  price_min: 65,
  price_max: 120,
  currency: 'GBP',
  artists: [{ name: 'Radiohead', headliner: true, server_id: 1, rating_key: 100 }],
  performers: [{ name: 'Radiohead', headliner: true }],
}
let gigs: unknown[] = [gig]

beforeEach(() => {
  gigs = [gig]
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
      switch (url.pathname) {
        case '/api/auth/me':
          return json({ id: 1, username: 'me', email: null, thumb_url: null, is_admin: false })
        case '/api/servers':
          return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: false, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
        case '/api/servers/1/sync':
          return json({ active: null, last: null, last_sync_at: null, counts: { artists: 1, albums: 1, tracks: 2, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
        case '/api/servers/1/library/artists/100':
          return json({
            rating_key: 100, title: 'Radiohead', thumb: null, popularity: null, user_rating: null, plays: 5,
            album_count: 0, summary: null, art: null, tags: [], similar: [], albums: [], top_tracks: [],
            your_listens: { listens: 0, first_at: null, last_at: null, years: [], songs: [], artist_keys: [], album_key: null },
          })
        case '/api/live/artists/1/100/gigs':
          return json(gigs)
        default:
          return json({ detail: 'Not found' }, 404)
      }
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderArtist() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/library/artists/100']}>
        <Routes>
          <Route path="/library/artists/:key" element={<ArtistPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Gigs on artist pages', () => {
  it('lists upcoming gigs and marks the picture', async () => {
    renderArtist()
    const card = (await screen.findByRole('heading', { name: 'Upcoming gigs' })).closest('section, div')!
    expect(within(card.parentElement!).getByText(/The O2 · London/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Tickets' })).toHaveAttribute('href', 'https://tickets.example/rh')
    expect(screen.getByRole('img', { name: 'Upcoming gig' })).toBeInTheDocument()
  })

  it('shows neither without gigs', async () => {
    gigs = []
    renderArtist()
    expect(await screen.findByRole('heading', { name: 'Radiohead' })).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByText('Upcoming gigs')).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: 'Upcoming gig' })).not.toBeInTheDocument()
  })
})
