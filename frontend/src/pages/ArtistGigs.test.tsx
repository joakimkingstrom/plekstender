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
            album_count: 0, summary: null, art: null, tags: [], similar: [], albums: [],
            top_tracks: [
              { rating_key: 300, title: 'Paranoid Android', original_title: null, disc: 1, number: 2, duration_ms: 1000, popularity: null, user_rating: null, plays: 0, album_rating_key: 200, album_title: 'OK Computer', album_thumb: null, listens: 0, seen_live: 2 },
              { rating_key: 301, title: 'Airbag', original_title: null, disc: 1, number: 1, duration_ms: 1000, popularity: null, user_rating: null, plays: 0, album_rating_key: 200, album_title: 'OK Computer', album_thumb: null, listens: 0, seen_live: 0 },
            ],
            your_listens: { listens: 0, first_at: null, last_at: null, years: [], songs: [], artist_keys: [], album_key: null },
          })
        case '/api/live/artists/1/100/gigs':
          return json(gigs)
        case '/api/gigs/artist/1/100':
          return json([
            {
              id: 3, date: '2017-06-24', name: 'Glastonbury 2017', venue: 'Pyramid Stage', city: 'Pilton', country: 'GB',
              latitude: null, longitude: null, place_precision: null, notes: null, source: 'setlistfm', url: null,
              artists: [{ name: 'Radiohead', server_id: 1, rating_key: 100 }, { name: 'Young Fathers', server_id: null, rating_key: null }],
              setlists: [{ artist: 'Radiohead', url: 'https://www.setlist.fm/setlist/x-63de4613.html', songs: [
                { name: 'Paranoid Android', cover: null, tape: false },
                { name: 'Intro', cover: null, tape: true },
              ] }],
            },
          ])
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

  it("lists the gigs you've been to, with the setlist, and marks songs seen live", async () => {
    renderArtist()
    const heading = await screen.findByRole('heading', { name: "Gigs you've been to" })
    const card = heading.closest('section, div')!.parentElement!
    expect(within(card).getByText('Glastonbury 2017 · Pyramid Stage')).toBeInTheDocument()
    expect(within(card).getByText('Pilton · with Young Fathers')).toBeInTheDocument()
    within(card).getByRole('button', { name: 'Setlist (2 songs)' }).click()
    expect(await within(card).findByText('(tape)')).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'On setlist.fm' })).toBeInTheDocument()
    // The popular songs: seen live twice, and one not.
    expect(screen.getByText(/Seen live 2×/)).toBeInTheDocument()
    expect(screen.getAllByText(/Seen live/)).toHaveLength(1)
  })
})
