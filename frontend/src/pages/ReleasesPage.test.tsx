import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { formatReleaseDate } from '../api/releases'
import { ReleasesPage } from './ReleasesPage'

const release = (n: number, title: string, extra: object = {}) => ({
  mbid: `0000000${n}-0000-4000-8000-000000000000`,
  title,
  kind: 'album',
  first_release_date: '2026-11-14',
  in_library: false,
  hidden: false,
  ...extra,
})
const radiohead = { rating_key: 100, name: 'Radiohead', thumb: null }

let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>
let preferences: Record<string, unknown>

beforeEach(() => {
  preferences = { releases: { kinds: ['album', 'ep'], follow_rating: 8, follow_top: 50 } }
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    const url = new URL(input.url)
    const json = (body: unknown, status = 200) =>
      new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    switch (url.pathname) {
      case '/api/auth/me/preferences':
        preferences = await input.clone().json()
        return json({ id: 1, username: 'me', email: null, thumb_url: null, is_admin: false, preferences })
      case '/api/auth/me':
        return json({ id: 1, username: 'me', email: null, thumb_url: null, is_admin: false, preferences })
      case '/api/servers':
        return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: false, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      case '/api/servers/1/sync':
        return json({ active: null, last: null, last_sync_at: null, counts: { artists: 2, albums: 1, tracks: 4, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
      case '/api/servers/1/releases/new':
        return json({
          items: [
            { ...release(7, 'Still to Come', { kind: 'ep', first_release_date: '2026-12' }), upcoming: true, artist: radiohead },
            { ...release(6, 'A Fresh One', { in_library: true }), upcoming: false, artist: radiohead },
          ],
          following: 12,
          new_days: 90,
        })
      case '/api/servers/1/releases/missing':
        return json({
          artists: [{ ...radiohead, releases: [release(2, 'Kid A', { first_release_date: '2000-10-02' }), release(3, 'Amnesiac', { first_release_date: '2001' })] }],
          total_artists: 3,
          coverage: { artists: 10, with_mbid: 8, checked: 5 },
        })
      default:
        if (url.pathname.startsWith('/api/servers/1/releases/hidden/')) return json(null, 204)
        return json({ detail: 'Not found' }, 404)
    }
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderAt(path: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <ReleasesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const requests = (path: string) => fetchMock.mock.calls.map(([r]) => new URL(r.url)).filter((u) => u.pathname === path)

it('formats release dates as precise as MusicBrainz knows them', () => {
  expect(formatReleaseDate('2001')).toBe('2001')
  expect(formatReleaseDate('2026-12')).toMatch(/December 2026/)
  expect(formatReleaseDate(null)).toBe('Date unknown')
})

it('shows new releases from followed artists, coming ones first, and hides one', async () => {
  renderAt('/library/releases')
  const coming = (await screen.findByRole('heading', { name: 'Coming up' })).closest('section')!
  expect(within(coming).getByText('Still to Come')).toBeInTheDocument()
  expect(within(coming).getByText(/EP · December 2026/)).toBeInTheDocument()
  const out = screen.getByRole('heading', { name: 'Out in the last 90 days' }).closest('section')!
  expect(within(out).getByText('In your library')).toBeInTheDocument()
  expect(within(out).getByRole('link', { name: 'Radiohead' })).toHaveAttribute('href', '/library/artists/100')
  expect(within(out).getByText(/From the 12 artists you follow/)).toBeInTheDocument()
  // Albums and EPs unless chosen otherwise.
  expect(requests('/api/servers/1/releases/new')[0].searchParams.getAll('kind')).toEqual(['album', 'ep'])
  // Kinds and whom you follow are saved on your account.
  fireEvent.click(await screen.findByRole('button', { name: 'Singles' }))
  await vi.waitFor(() =>
    expect(requests('/api/servers/1/releases/new').at(-1)!.searchParams.getAll('kind')).toEqual(['album', 'ep', 'single']),
  )
  expect(preferences.releases).toEqual({ kinds: ['album', 'ep', 'single'], follow_rating: 8, follow_top: 50 })
  expect(screen.getByRole('button', { name: /Follow artists rated at least/ })).toHaveTextContent('4 stars')
  fireEvent.click(screen.getByRole('button', { name: /Follow your most played artists/ }))
  fireEvent.click(await screen.findByRole('option', { name: '200' }))
  await vi.waitFor(() => expect(preferences.releases).toMatchObject({ follow_top: 200 }))
  fireEvent.click(within(out).getByRole('button', { name: 'Hide A Fresh One' }))
  await vi.waitFor(() =>
    expect(fetchMock.mock.calls.some(([r]) => r.method === 'PUT' && r.url.endsWith('/releases/hidden/00000006-0000-4000-8000-000000000000'))).toBe(true),
  )
})

it('lists what is missing by artist, with how far the MusicBrainz lookups are', async () => {
  renderAt('/library/releases?view=missing')
  const artist = (await screen.findByRole('link', { name: 'Radiohead' })).closest('section')!
  expect(within(artist).getByText('2 missing')).toBeInTheDocument()
  expect(within(artist).getByText('Album · 2001')).toBeInTheDocument()
  expect(within(artist).getByRole('link', { name: /Kid A/ })).toHaveAttribute('href', 'https://musicbrainz.org/release-group/00000002-0000-4000-8000-000000000000')
  expect(screen.getByText('3 artists have releases you don\'t')).toBeInTheDocument()
  expect(screen.getByText(/Still reading releases from MusicBrainz: 5 of 8 artists so far/)).toBeInTheDocument()
  expect(screen.getByText(/2 of your 10 artists have no MusicBrainz ID in Plex/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'More artists' })).toBeInTheDocument()
  fireEvent.change(screen.getByRole('searchbox', { name: 'Find an artist' }), { target: { value: 'radio' } })
  await vi.waitFor(() => expect(requests('/api/servers/1/releases/missing').at(-1)!.searchParams.get('q')).toBe('radio'))
})
