import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { RediscoverPage } from './RediscoverPage'

const song = (rating_key: number, title: string, extra: object) => ({
  rating_key,
  title,
  artist: 'Portishead',
  album: 'Dummy',
  album_rating_key: 202,
  thumb: null,
  rating: 10,
  plays: 0,
  last_played_at: null,
  hidden: false,
  ...extra,
})

let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>

beforeEach(() => {
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
      const url = new URL(input.url)
      const path = url.pathname
      if (path.startsWith('/api/stats/rediscover/hidden/')) return new Response(null, { status: 204 })
      const hiddenToo = url.searchParams.get('include_hidden') === 'true'
      const body: Record<string, unknown> = {
        '/api/servers': [{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }],
        '/api/stats/rediscover': {
          forgotten: { total: 41, items: [song(304, 'Glory Box', { plays: 12, last_played_at: '2024-03-01T12:00:00Z' })] },
          unplayed: { total: 1, items: [song(303, 'Lucky', { rating: 9, artist: 'Radiohead' })] },
          disliked: hiddenToo ? { total: 1, items: [song(301, 'Karma Police', { rating: 2, plays: 9, hidden: true })] } : { total: 0, items: [] },
          loved: 9,
          disliked_below: 4,
          forgotten_days: 365,
          hardly_played: 2,
          played_a_lot: 5,
        },
      }
      return new Response(JSON.stringify(body[path] ?? { detail: 'Not found' }), {
        status: path in body ? 200 : 404,
        headers: { 'content-type': 'application/json' },
      })
    })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('lists songs whose ratings and plays disagree, with a playlist for the forgotten ones', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <RediscoverPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const forgotten = (await screen.findByRole('heading', { name: 'Forgotten favourites' })).closest('section')!
  expect(within(forgotten).getByText('Rated 4½ stars or more, but not played for a year.')).toBeInTheDocument()
  expect(within(forgotten).getByRole('link', { name: 'Glory Box' })).toHaveAttribute('href', '/library/albums/202')
  expect(within(forgotten).getByText(/Portishead · 12 plays · last/)).toBeInTheDocument()
  expect(within(forgotten).getByText('Showing 1 of 41.')).toBeInTheDocument()
  expect(within(forgotten).getByRole('link', { name: 'Make a playlist' })).toHaveAttribute('href', '/library/playlists/new?forgotten=1')
  expect(within(forgotten).getByRole('slider', { name: 'Rate Glory Box' })).toBeInTheDocument()

  const unplayed = screen.getByRole('heading', { name: 'Loved but hardly played' }).closest('section')!
  expect(within(unplayed).getByText('Rated 4½ stars or more, played 2 times at most.')).toBeInTheDocument()
  expect(within(unplayed).getByText('Radiohead · 0 plays · never played')).toBeInTheDocument()

  const disliked = screen.getByRole('heading', { name: 'Played a lot, rated low' }).closest('section')!
  expect(within(disliked).getByText('Rated 2 stars or less, but played 5 times or more.')).toBeInTheDocument()
  expect(within(disliked).getByText('None: what you play a lot, you rate well.')).toBeInTheDocument()

  // Hiding a song; hidden ones can be listed, and shown again.
  fireEvent.click(within(forgotten).getByRole('button', { name: 'Hide Glory Box' }))
  await vi.waitFor(() =>
    expect(fetchMock.mock.calls.some(([r]) => r.method === 'PUT' && new URL(r.url).pathname === '/api/stats/rediscover/hidden/304')).toBe(true),
  )
  fireEvent.click(screen.getByRole('checkbox', { name: 'Show hidden songs' }))
  const show = await within(disliked).findByRole('button', { name: 'Show Karma Police' })
  fireEvent.click(show)
  await vi.waitFor(() =>
    expect(fetchMock.mock.calls.some(([r]) => r.method === 'DELETE' && new URL(r.url).pathname === '/api/stats/rediscover/hidden/301')).toBe(true),
  )
})
