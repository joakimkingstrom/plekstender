import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
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
  ...extra,
})

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname
      const body: Record<string, unknown> = {
        '/api/servers': [{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }],
        '/api/stats/rediscover': {
          forgotten: { total: 41, items: [song(304, 'Glory Box', { plays: 12, last_played_at: '2024-03-01T12:00:00Z' })] },
          unplayed: { total: 1, items: [song(303, 'Lucky', { rating: 9, artist: 'Radiohead' })] },
          disliked: { total: 0, items: [] },
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
    }),
  )
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
})
