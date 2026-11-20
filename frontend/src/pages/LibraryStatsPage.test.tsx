import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LibraryStatsPage } from './LibraryStatsPage'

beforeEach(() => {
  // jsdom has no layout: give the charts a width.
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(600)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      switch (url.pathname) {
        case '/api/auth/me':
          return json({ id: 1, username: 'me', email: null, thumb_url: null, is_admin: false })
        case '/api/servers':
          return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: false, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
        case '/api/servers/1/sync':
          return json({ active: null, last: null, last_sync_at: null, counts: { artists: 3, albums: 4, tracks: 6, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
        case '/api/servers/1/library/stats':
          return json({
            artists: 3, albums: 4, tracks: 1234,
            growth: [
              { month: '2025-12', artists: 1, albums: 1, tracks: 600 },
              { month: '2026-01', artists: 3, albums: 4, tracks: 1234 },
            ],
            decades: [{ decade: 1990, albums: 2 }, { decade: 2010, albums: 2 }],
            undated_albums: 1,
          })
        case '/api/servers/1/library/stats/released':
          if (url.searchParams.get('year'))
            return json({
              buckets: Array.from({ length: 13 }, (_, m) => ({ decade: m, albums: m === 5 ? 1 : 0 })),
              albums: [{ rating_key: 200, title: 'OK Computer', thumb: null, released: '1997-05-21', artist_title: 'Radiohead', plays: 3 }],
            })
          return json({ buckets: Array.from({ length: 10 }, (_, i) => ({ decade: 1990 + i, albums: i === 7 ? 2 : 0 })), albums: [] })
        case '/api/servers/1/library/stats/countries':
          return json({ countries: [{ country_code: 'GB', artists: 2 }, { country_code: 'SE', artists: 1 }], unknown: 4, pending: 3 })
        case '/api/servers/1/library/stats/countries/SE':
          return json([{ rating_key: 105, title: 'The Knife', thumb: null, popularity: null, user_rating: null, plays: 0, album_count: 1 }])
        case '/api/servers/1/library/stats/colours':
          return json({ families: [{ name: 'red', albums: 1, hex: '#c81e1e' }, { name: 'blue', albums: 2, hex: '#2850c8' }], strip: ['#c81e1e', '#2850c8', '#2850c8'], albums: 3, waiting: 1 })
        case '/api/servers/1/library/stats/colours/blue':
          return json([{ rating_key: 200, title: 'OK Computer', thumb: null, artist_title: 'Radiohead', hex: '#2850c8' }])
        case '/api/servers/1/library/stats/words':
          return json([{ word: 'Love', count: 40 }, { word: 'Karma', count: 2 }])
        default:
          return json({ detail: 'Not found' })
      }
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Library stats', () => {
  it('shows totals, growth, decades down to months, and words', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <LibraryStatsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByText('1,234')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Total tracks by when they were added to Plex' })).toBeInTheDocument()
    expect(screen.getByText(/1 album without a year isn't counted/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'The 1990s: 2 albums' }))
    fireEvent.click(await screen.findByRole('button', { name: '1997: 2 albums' }))
    expect(await screen.findByRole('link', { name: 'OK Computer' })).toHaveAttribute('href', '/library/albums/200')
    expect(screen.getByRole('button', { name: 'The 1990s' })).toBeInTheDocument()

    expect(await screen.findByTitle('Love: in 40 names')).toBeInTheDocument()

    // Where artists are from: a country's artists.
    expect(screen.getByText(/3 artists placed/)).toBeInTheDocument()
    expect(screen.getByText(/4 without a country \(3 still to be looked up/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Sweden/ }))
    expect(await screen.findByRole('link', { name: 'The Knife' })).toHaveAttribute('href', '/library/artists/105')

    // Cover colours: a family's covers.
    expect(screen.getByText(/1 cover still to be looked at/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Blue: 2 albums' }))
    expect(await screen.findByTitle('OK Computer · Radiohead')).toHaveAttribute('href', '/library/albums/200')
  })
})
