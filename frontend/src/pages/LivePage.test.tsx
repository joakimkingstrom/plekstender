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
  period_months: null as number | null,
  listened_artists: 40,
  followed_artists: 4,
  refresh_status: null as string | null,
  refresh_error: null,
  refreshed_at: null as string | null,
  refresh_available_at: null,
  source_ready: true,
  sources: ['jambase', 'ticketmaster'],
  datathistle_at: null as string | null,
  datathistle_error: null as string | null,
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
  ids: [1, 3],
  source: 'jambase',
  sources: ['jambase', 'ticketmaster'],
  starred: false,
  got_ticket: false,
  tickets: [
    { source: 'jambase', url: 'https://www.ticketmaster.se/event/radiohead', seller: 'Ticketmaster', is_ticket: true },
    { source: 'ticketmaster', url: 'https://www.ticketmaster.se/event/A1', seller: 'Ticketmaster', is_ticket: true },
  ],
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
  ids: [2],
  sources: ['jambase'],
  starred: true,
  got_ticket: false,
  tickets: [{ source: 'jambase', url: 'https://www.jambase.com/show/radiohead', seller: null, is_ticket: false }],
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
let feeds: Record<string, unknown>[] = []
let fetchMock: ReturnType<typeof vi.fn<(input: Request | string, init?: RequestInit) => Promise<Response>>>

beforeEach(() => {
  settings = base
  feeds = []
  festival.got_ticket = false
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
      case '/api/live/listened':
        return json({ listened_artists: url.searchParams.get('period_months') ? 12 : 40 })
      case '/api/live/tickets':
        festival.got_ticket = (await request!.clone().json()).got_ticket
        return new Response(null, { status: 204 })
      case '/api/live/stars':
        return new Response(null, { status: 204 })
      case '/api/live/calendar':
        return json({ url: 'https://plek.example/api/live/calendar/secret-token-1234567890.ics' })
      case '/api/live/feeds':
        if (request?.method === 'POST') {
          const { url: added } = await request.clone().json()
          if (!added.startsWith('webcal://www.songkick.com')) return json({ detail: "That address isn't a calendar (iCal) file." }, 422)
          feeds.push({ id: 9, host: 'songkick.com', name: 'Songkick: tracked artists', fetched_at: null, error: null, show_count: null })
          return json(feeds.at(-1), 201)
        }
        return json(feeds)
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
    expect([0, 1, 9, 25, 5000].map((n) => followCount(n, 10))).toEqual([0, 1, 1, 3, 500])
  })

  it('sets up a place, a distance and the artists to follow', async () => {
    renderPage()
    expect(await screen.findByText("Your 4 most played of the 40 artists you've played in all time.")).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search for a city' }), { target: { value: 'Stock' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Stockholm, Stockholm County, Sweden' }))
    fireEvent.change(screen.getByRole('slider'), { target: { value: '25' } })
    expect(screen.getByText("Your 10 most played of the 40 artists you've played in all time.")).toBeInTheDocument()
    // The past 6 months instead of all time.
    fireEvent.click(screen.getByRole('button', { name: /^Most played over/ }))
    fireEvent.click(screen.getByRole('option', { name: 'The past…' }))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'How many' }), { target: { value: '6' } })
    fireEvent.click(screen.getByRole('button', { name: /^Months or years/ }))
    fireEvent.click(screen.getByRole('option', { name: 'months' }))
    expect(await screen.findByText("Your 3 most played of the 12 artists you've played in the past 6 months.")).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await vi.waitFor(async () => {
      const [put] = requestsTo('/api/live/settings').filter((r) => r.method === 'PUT')
      expect(await put.clone().json()).toEqual({
        place_name: 'Stockholm, Stockholm County, Sweden',
        latitude: 59.33,
        longitude: 18.07,
        radius_km: 100,
        top_percent: 25,
        period_months: 6,
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
    expect(screen.getByRole('link', { name: 'Shows also from Ticketmaster' })).toBeInTheDocument()
    // Found by both sources: Ticketmaster's own link too.
    expect(within(row).getByRole('link', { name: 'Also on Ticketmaster' })).toHaveAttribute('href', 'https://www.ticketmaster.se/event/A1')
    // A starred show can be added to a calendar by hand.
    expect(screen.getByRole('link', { name: 'Add to calendar' })).toHaveAttribute('href', '/api/live/shows/2.ics')
    // Only starred shows offer "I've got a ticket".
    expect(within(row).queryByRole('button', { name: "I've got a ticket" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: "I've got a ticket" }))
    expect(await screen.findByText('🎟 Got a ticket')).toBeInTheDocument()
    await vi.waitFor(async () => {
      const put = requestsTo('/api/live/tickets').at(-1)
      expect(put && (await put.clone().json())).toEqual({ ids: [2], got_ticket: true })
    })

    fireEvent.click(within(row).getByRole('button', { name: 'Star' }))
    await vi.waitFor(async () => {
      const put = requestsTo('/api/live/stars').at(-1)
      expect(put && (await put.clone().json())).toEqual({ ids: [1, 3], starred: true })
    })

    fireEvent.click(screen.getByRole('button', { name: /^Artist/ }))
    fireEvent.click(screen.getByRole('option', { name: 'Portishead (1)' }))
    await vi.waitFor(() => expect(screen.queryByRole('link', { name: 'Radiohead' })).not.toBeInTheDocument())
    expect(requestsTo('/api/live/shows').at(-1)!.url).toContain('artist=Portishead')
    expect(screen.getByRole('link', { name: 'Find Portishead on Songkick' })).toHaveAttribute(
      'href',
      'https://www.songkick.com/search?type=artists&query=Portishead',
    )
  })

  it('shows the private calendar address to subscribe to', async () => {
    settings = placed
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Show calendar address' }))
    expect(await screen.findByRole('textbox', { name: 'Calendar address' })).toHaveValue(
      'https://plek.example/api/live/calendar/secret-token-1234567890.ics',
    )
    expect(screen.getByRole('link', { name: 'Subscribe' })).toHaveAttribute(
      'href',
      'webcal://plek.example/api/live/calendar/secret-token-1234567890.ics',
    )
  })

  it('adds a calendar link', async () => {
    settings = placed
    renderPage()
    const input = await screen.findByRole('textbox', { name: 'Calendar link' })
    fireEvent.change(input, { target: { value: 'https://example.com/not-a-calendar' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add link' }))
    expect(await screen.findByText("That address isn't a calendar (iCal) file.")).toBeInTheDocument()
    fireEvent.change(input, { target: { value: 'webcal://www.songkick.com/users/someone/calendars.ics' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add link' }))
    expect(await screen.findByText('Songkick: tracked artists')).toBeInTheDocument()
    expect(screen.getByText('Not read yet')).toBeInTheDocument()
  })

  it('credits Data Thistle with its logo and event links', async () => {
    settings = { ...placed, sources: ['jambase', 'datathistle'], datathistle_error: 'Today’s Data Thistle requests are used up.' }
    renderPage()
    expect(await screen.findByRole('img', { name: 'Data Thistle' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add or update an event' })).toHaveAttribute(
      'href',
      'https://www.datathistle.com/update/event-listing/',
    )
    expect(screen.getByText(/Data Thistle: Today’s Data Thistle requests are used up/)).toBeInTheDocument()
  })

  it('says when JamBase has no key yet', async () => {
    settings = { ...placed, source_ready: false, sources: [] }
    renderPage()
    expect(await screen.findByText(/Live needs a JamBase or Ticketmaster API key/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeDisabled()
  })
})
