import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GigExportCard, GigImportCard } from '../components/GigTransfer'
import { GigsPage } from './GigsPage'

// The map needs WebGL, which jsdom hasn't got (gigMap.test covers how gigs are placed on it).
vi.mock('../components/GigMap', () => ({ default: () => <div role="region" aria-label="Gig map" /> }))

const gigs = [
  {
    id: 1, date: '2019-10-02', name: null, venue: 'Royal Albert Hall', city: 'London', country: 'GB',
    latitude: 51.5072, longitude: -0.1276, place_precision: 'city', notes: null, source: 'manual', url: null,
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
const gigLists = () =>
  fetchMock.mock.calls
    .map(([input]) => new URL(typeof input === 'string' ? input : input.url, 'http://localhost'))
    .filter((url) => url.pathname === '/api/gigs')
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
        return json({
          gigs, years: [{ year: 2019, gigs: 1 }, { year: 2017, gigs: 1 }], total: 2, unplaced: 2,
          places: [
            { country: 'GB', city: 'London', venue: 'Royal Albert Hall', gigs: 1 },
            { country: 'GB', city: 'Pilton', venue: 'Pyramid Stage', gigs: 1 },
            { country: 'SE', city: 'Stockholm', venue: 'Annexet', gigs: 1 },
          ],
        })  // prettier-ignore
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
      case '/api/gigs/setlistfm/search':
        return json({
          items: [{ url: 'https://www.setlist.fm/setlist/x-63de4613.html', date: '2017-06-24', artist: 'Radiohead', venue: 'Worthy Farm', city: 'Pilton', country: 'GB', tour: 'Glastonbury 2017', songs: 24 }],
          total: 1, page: 1,
        })
      case '/api/gigs/setlistfm/lookup':
        return json({
          date: '2017-06-24', name: 'Glastonbury 2017', venue: 'Worthy Farm', city: 'Pilton', country: 'GB',
          artists: ['Radiohead', 'Support Act'], url: 'https://www.setlist.fm/setlist/x-63de4613.html',
          setlists: [
            { artist: 'Radiohead', url: null, songs: [{ name: 'Paranoid Android', cover: null, tape: false }, { name: 'Creep', cover: null, tape: false }] },
            { artist: 'Support Act', url: null, songs: [{ name: 'Opener', cover: null, tape: false }] },
          ],
        })
      case '/api/gigs/import/setlistfm':
        return json({ added: 12, updated: 3, skipped: 1, problems: [] })
      case '/api/gigs/places':
        return json([{ name: 'Royal Albert Hall', address: 'Kensington Gore, London, United Kingdom', kind: 'theatre', latitude: 51.5009, longitude: -0.1774 }])
      case '/api/gigs/1':
        return json(gigs[0])
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
  })

  it('narrows gigs by year, place and venues not found on the map', async () => {
    renderPage()
    await screen.findByRole('heading', { name: /2019/ })
    const lastList = () => {
      const url = gigLists().at(-1)!
      return Object.fromEntries(url.searchParams)
    }
    fireEvent.click(screen.getByRole('button', { name: 'Filter by year' }))
    fireEvent.click(screen.getByRole('option', { name: '2019' }))
    await vi.waitFor(() => expect(lastList()).toEqual({ year: '2019' }))

    // A venue brings its city and country with it; a country clears what's below it.
    fireEvent.click(screen.getByRole('button', { name: 'Filter by venue' }))
    expect(screen.getAllByRole('option').map((o) => o.textContent?.replace('✓', ''))).toEqual([
      'All venues', 'Annexet · Stockholm', 'Pyramid Stage · Pilton', 'Royal Albert Hall · London',
    ])  // prettier-ignore
    fireEvent.click(screen.getByRole('option', { name: 'Annexet · Stockholm' }))
    await vi.waitFor(() => expect(lastList()).toEqual({ year: '2019', country: 'SE', city: 'Stockholm', venue: 'Annexet' }))
    fireEvent.click(screen.getByRole('button', { name: 'Filter by country' }))
    fireEvent.click(screen.getByRole('option', { name: 'United Kingdom' }))
    await vi.waitFor(() => expect(lastList()).toEqual({ year: '2019', country: 'GB' }))
    fireEvent.click(screen.getByRole('button', { name: 'Filter by city' }))
    expect(screen.getAllByRole('option').map((o) => o.textContent?.replace('✓', ''))).toEqual(['All cities', 'London', 'Pilton'])
    fireEvent.click(screen.getByRole('option', { name: 'All cities' }))

    fireEvent.click(screen.getByRole('button', { name: '📍 Venue not found (2)' }))
    await vi.waitFor(() => expect(lastList()).toEqual({ year: '2019', country: 'GB', unplaced: 'true' }))
    expect(screen.getByText(/Edit a gig to pick its place by hand/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    await vi.waitFor(() => expect(lastList()).toEqual({}))
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
    expect(screen.getByRole('dialog', { name: 'Add a gig' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add it by hand' }))
    // The date picker: the title goes to months, then years; then 2024, May, the 1st.
    fireEvent.click(screen.getByLabelText('Date'))
    const title = screen.getByRole('dialog', { name: 'Choose a date' }).querySelector('[aria-live]')!
    fireEvent.click(title)
    fireEvent.click(title)
    fireEvent.click(screen.getByRole('button', { name: '2024' }))
    fireEvent.click(screen.getByRole('button', { name: 'May' }))
    fireEvent.click(screen.getByRole('button', { name: /May 1, 2024|1 May 2024/ }))
    expect(screen.queryByRole('dialog', { name: 'Choose a date' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Date')).toHaveTextContent(/2024/)
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
        setlists: [],
        place: null,
        find_place: false,
      }),
    )
  })

  it('picks a gig’s place by hand when only the town was found', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Edit the gig on 2019-10-02' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('The venue wasn’t found, so it’s shown at London.')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Pick it yourself' }))
    expect(within(dialog).getByLabelText('Search for the place')).toHaveValue('Royal Albert Hall, London')
    fireEvent.click(await within(dialog).findByRole('button', { name: /Royal Albert Hall.*Kensington Gore/ }))
    expect(within(dialog).getByText('Picked: Royal Albert Hall, Kensington Gore, London, United Kingdom.')).toBeInTheDocument()
    const asked = new URL(calls('/api/gigs/places', 'GET')[0].url)
    expect([asked.searchParams.get('q'), asked.searchParams.get('country')]).toEqual(['Royal Albert Hall, London', 'GB'])
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await vi.waitFor(async () => {
      const put = calls('/api/gigs/1', 'PUT')[0]
      expect(put).toBeTruthy()
      expect(await put.clone().json()).toMatchObject({ place: { latitude: 51.5009, longitude: -0.1774 }, find_place: false })
    })
  })

  it('fills a gig in from a setlist.fm link', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a gig' }))
    const link = 'https://www.setlist.fm/setlist/radiohead/2017/worthy-farm-pilton-england-63de4613.html'
    fireEvent.change(screen.getByLabelText('A setlist.fm link'), { target: { value: link } })
    expect(await screen.findByText(/Filled in from setlist\.fm: 2 artists and 3 songs played/)).toBeInTheDocument()
    expect(await calls('/api/gigs/setlistfm/lookup')[0].clone().json()).toEqual({ url: link })
    expect(screen.getByLabelText(/^Artists/)).toHaveValue('Radiohead\nSupport Act')
    expect(screen.getByLabelText('Venue')).toHaveValue('Worthy Farm')
    expect(screen.getByText(/Radiohead \(2 songs\), Support Act \(1 song\)/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await vi.waitFor(async () => {
      const saved = await calls('/api/gigs')[0]?.clone().json()
      expect(saved).toMatchObject({ date: '2017-06-24', name: 'Glastonbury 2017', country: 'GB' })
      expect(saved.setlists.map((s: { artist: string }) => s.artist)).toEqual(['Radiohead', 'Support Act'])
    })
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('finds a gig by searching setlist.fm', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a gig' }))
    const popup = within(screen.getByRole('dialog', { name: 'Add a gig' }))
    fireEvent.change(popup.getByLabelText('Artist'), { target: { value: 'Radiohead' } })
    fireEvent.change(popup.getByLabelText('Year'), { target: { value: '2017' } })
    fireEvent.click(popup.getByRole('button', { name: 'Search' }))
    const results = await screen.findByRole('list', { name: 'Setlists found' })
    const asked = new URL(calls('/api/gigs/setlistfm/search', 'GET')[0].url)
    expect([asked.searchParams.get('artist'), asked.searchParams.get('year'), asked.searchParams.get('city')]).toEqual(['Radiohead', '2017', null])
    expect(results).toHaveTextContent('Glastonbury 2017 · Worthy Farm · Pilton · United Kingdom')
    fireEvent.click(screen.getByRole('button', { name: /Radiohead.*24 songs/ }))
    expect(await screen.findByText(/Filled in from setlist\.fm/)).toBeInTheDocument()
    expect(await calls('/api/gigs/setlistfm/lookup')[0].clone().json()).toEqual({ url: 'https://www.setlist.fm/setlist/x-63de4613.html' })
  })

  it('points to Settings for importing and exporting', async () => {
    renderPage()
    expect(await screen.findByRole('link', { name: 'Settings → Export & import' })).toHaveAttribute('href', '/settings/export/gigs')
    expect(screen.queryByLabelText('setlist.fm username')).not.toBeInTheDocument()
  })
})

describe('Gig import and export (Settings → Export & import → Gigs)', () => {
  it('imports from setlist.fm and offers exports', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <GigImportCard />
        <GigExportCard />
      </QueryClientProvider>,
    )
    fireEvent.change(await screen.findByLabelText('setlist.fm username'), { target: { value: ' joakim ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Import the concerts I went to' }))
    expect(await screen.findByText(/Added 12, updated 3, 1 already there\./)).toBeInTheDocument()
    expect(await calls('/api/gigs/import/setlistfm')[0].clone().json()).toEqual({ username: 'joakim' })
    expect(screen.getByRole('link', { name: 'CSV' })).toHaveAttribute('href', '/api/gigs/export.csv')
    expect(screen.getByRole('link', { name: 'Calendar (.ics)' })).toHaveAttribute('href', '/api/gigs/export.ics')
  })
})
