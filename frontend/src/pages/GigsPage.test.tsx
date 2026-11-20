import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GigsPage } from './GigsPage'

const gigs = [
  {
    id: 1, date: '2019-10-02', name: null, venue: 'Royal Albert Hall', city: 'London', country: 'GB',
    latitude: null, longitude: null, place_precision: null, notes: null, source: 'manual', url: null,
    artists: [{ name: 'Portishead', server_id: 1, rating_key: 101 }],
  },
  {
    id: 2, date: '2017-06-24', name: 'Glastonbury 2017', venue: 'Pyramid Stage', city: 'Pilton', country: 'GB',
    latitude: null, longitude: null, place_precision: null, notes: 'Paranoid Android!', source: 'manual', url: null,
    artists: [{ name: 'Radiohead', server_id: 1, rating_key: 100 }],
  },
]
const suggestion = {
  id: 9, ids: [9, 10], source: 'jambase', sources: ['jambase', 'ticketmaster'], starred: true, got_ticket: true,
  first_seen_at: null, is_new: false, tickets: [], name: 'Radiohead', festival: false, start_date: '2026-11-15',
  start_time: '19:30', end_date: null, status: 'scheduled', venue: 'The O2', city: 'London', region: null,
  country: 'GB', distance_km: 10, ticket_url: null, seller: null, page_url: null, price_min: null, price_max: null,
  currency: null, artists: [{ name: 'Radiohead', headliner: true, server_id: 1, rating_key: 100 }], performers: [],
}  // prettier-ignore

let fetchMock: ReturnType<typeof vi.fn<(input: Request | string) => Promise<Response>>>
const calls = (path: string, method = 'POST') =>
  fetchMock.mock.calls
    .map(([input]) => input)
    .filter((r): r is Request => typeof r !== 'string' && new URL(r.url).pathname === path && r.method === method)

beforeEach(() => {
  fetchMock = vi.fn(async (input: Request | string) => {
    const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    switch (url.pathname) {
      case '/api/servers':
        return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      case '/api/gigs':
        if (typeof input !== 'string' && input.method === 'POST') return json({ ...gigs[0], id: 3 }, 201)
        return json({ gigs, years: [{ year: 2019, gigs: 1 }, { year: 2017, gigs: 1 }], total: 2 })
      case '/api/gigs/artists':
        return json(['Portishead', 'Radiohead'])
      case '/api/gigs/suggestions':
        return json([suggestion])
      case '/api/gigs/stats':
        return json({
          total: 2, artists: 2, venues: 2, first: '2017-06-24', last: '2019-10-02',
          top_artists: [{ name: 'Radiohead', gigs: 1, server_id: 1, rating_key: 100 }, { name: 'Portishead', gigs: 1, server_id: 1, rating_key: 101 }],
          top_venues: [{ name: 'Royal Albert Hall', city: 'London', gigs: 1 }],
          years: [{ year: 2017, gigs: 1 }, { year: 2018, gigs: 0 }, { year: 2019, gigs: 1 }],
          months: [0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0],
          seasons: { winter: 0, spring: 0, summer: 1, autumn: 1 },
        })
      case '/api/gigs/import/setlistfm':
        return json({ added: 12, updated: 3, skipped: 1, problems: [] })
      case '/api/gigs/from-live':
        return json({ ...gigs[1], id: 4, source: 'live' }, 201)
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
        <GigsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('GigsPage', () => {
  it('lists gigs by year, linked to the library', async () => {
    renderPage()
    expect(await screen.findByRole('heading', { name: /2019/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /2017/ })).toBeInTheDocument()
    const links = await screen.findAllByRole('link', { name: 'Radiohead' })
    expect(links.map((l) => l.getAttribute('href'))).toEqual(['/library/artists/100', '/library/artists/100'])
    expect(screen.getByText('Glastonbury 2017')).toBeInTheDocument()
    expect(screen.getByText('Paranoid Android!')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '2019' })).toBeInTheDocument()
  })

  it('shows stats: busiest year, most seen artists and venues, time of year', async () => {
    renderPage()
    expect(await screen.findByText('Busiest year')).toBeInTheDocument()
    expect(screen.getByText('Most seen artists')).toBeInTheDocument()
    expect(screen.getByText(/by season winter 0, spring 0, summer 1, autumn 1/)).toBeInTheDocument()
    expect(screen.getByText('Royal Albert Hall', { selector: 'li span' })).toBeInTheDocument()
  })

  it('records a starred Live gig that has happened', async () => {
    renderPage()
    expect(await screen.findByText('Did you go?')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'I went' }))
    await vi.waitFor(async () => expect(await calls('/api/gigs/from-live')[0]?.clone().json()).toEqual({ ids: [9, 10] }))
  })

  it('adds a gig by hand', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a gig' }))
    fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2024-05-01' } })
    fireEvent.change(screen.getByLabelText(/^Artists/), { target: { value: 'Massive Attack\nYoung Fathers\n' } })
    fireEvent.change(screen.getByLabelText('Venue'), { target: { value: 'Brixton Academy' } })
    fireEvent.change(screen.getByLabelText('Country'), { target: { value: 'gb' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await vi.waitFor(async () =>
      expect(await calls('/api/gigs')[0]?.clone().json()).toEqual({
        date: '2024-05-01',
        artists: ['Massive Attack', 'Young Fathers'],
        name: null,
        venue: 'Brixton Academy',
        city: null,
        country: 'GB',
        notes: null,
        url: null,
      }),
    )
  })

  it('imports from setlist.fm and offers exports', async () => {
    renderPage()
    fireEvent.change(await screen.findByLabelText('setlist.fm username'), { target: { value: ' joakim ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Import the concerts I went to' }))
    expect(await screen.findByText(/Added 12, updated 3, 1 already there\./)).toBeInTheDocument()
    expect(await calls('/api/gigs/import/setlistfm')[0].clone().json()).toEqual({ username: 'joakim' })
    expect(screen.getByRole('link', { name: 'CSV' })).toHaveAttribute('href', '/api/gigs/export.csv')
    expect(screen.getByRole('link', { name: 'Calendar (.ics)' })).toHaveAttribute('href', '/api/gigs/export.ics')
  })
})
