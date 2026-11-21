import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UpcomingGigs } from './HomeSections'

const gig = (id: number, artist: string, extra: { starred?: boolean; got_ticket?: boolean } = {}) => ({
  id,
  ids: [id],
  source: 'jambase',
  sources: ['jambase'],
  starred: false,
  got_ticket: false,
  tickets: [],
  name: `${artist} at Debaser`,
  festival: false,
  start_date: '2027-03-04',
  start_time: '20:00',
  end_date: null,
  status: 'scheduled',
  venue: 'Debaser',
  city: 'Stockholm',
  region: null,
  country: 'SE',
  distance_km: 2,
  first_seen_at: '2026-11-01T00:00:00Z',
  is_new: false,
  price_min: null,
  price_max: null,
  currency: null,
  ticket_url: null,
  seller: null,
  page_url: null,
  artists: [{ name: artist, headliner: true, server_id: 1, rating_key: id }],
  performers: [{ name: artist, headliner: true }],
  ...extra,
})

function serve(starred: unknown[], all: unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/live/settings') return json({ place_name: 'Stockholm, Sweden', latitude: 59.3, longitude: 18.1 })
      if (url.pathname === '/api/live/shows')
        return json({ shows: url.searchParams.get('starred') === 'true' ? starred : all, artists: [], venues: [] })
      return json({})
    }),
  )
}

const wrap = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <UpcomingGigs serverId={1} />
      </MemoryRouter>
    </QueryClientProvider>,
  )

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('UpcomingGigs', () => {
  it("shows the gigs you've got tickets for first", async () => {
    const radiohead = gig(1, 'Radiohead', { starred: true, got_ticket: true })
    const massive = gig(2, 'Massive Attack', { starred: true }) // starred, no ticket
    serve([radiohead, massive], [radiohead, massive, gig(3, 'Portishead')])
    wrap()
    expect(await screen.findByText("Gigs you've got tickets for")).toBeInTheDocument()
    expect(screen.getByText('Radiohead')).toBeInTheDocument()
    expect(screen.queryByText('Massive Attack')).not.toBeInTheDocument()
    expect(screen.queryByText('Portishead')).not.toBeInTheDocument()
  })

  it('shows the gigs coming up when you have no tickets', async () => {
    serve([gig(2, 'Massive Attack', { starred: true })], [gig(3, 'Portishead')])
    wrap()
    expect(await screen.findByText('Gigs coming up')).toBeInTheDocument()
    expect(await screen.findByText('Portishead')).toBeInTheDocument()
  })
})
