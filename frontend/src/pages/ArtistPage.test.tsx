import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ArtistPage } from './LibraryPage'

const album = (key: number, title: string) => ({
  rating_key: key, title, year: 1997, thumb: `/library/metadata/${key}/thumb/1`, user_rating: null, plays: 0,
  popularity: null, release_type: 'Album', artist_rating_key: 100, artist_title: 'Radiohead',
})

const artist = (key: number, title: string) => ({
  rating_key: key, title, thumb: null, popularity: null, user_rating: null, plays: 5, album_count: 2,
  summary: null, art: null, country: null, tags: [], similar: [],
  albums: [album(200, 'OK Computer'), album(201, 'Kid A')], top_tracks: [],
  your_listens: { listens: 0, first_at: null, last_at: null, years: [], songs: [], artist_keys: [], album_key: null },
})

beforeEach(() => {
  localStorage.removeItem('artist-cover-size')
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
      const m = url.pathname.match(/^\/api\/servers\/1\/library\/artists\/(\d+)$/)
      if (m) return json(artist(Number(m[1]), { 99: 'Portishead', 100: 'Radiohead', 101: 'Sigur Rós' }[Number(m[1])] ?? '?'))
      if (url.pathname === '/api/servers') return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: false, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      if (url.pathname === '/api/servers/1/sync') return json({ active: null, last: null, last_sync_at: null, counts: { artists: 3, albums: 2, tracks: 4, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
      return json([], 200)
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderArtist(key = 100) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[`/library/artists/${key}`]}>
        <Routes>
          <Route path="/library/artists/:key" element={<ArtistPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Artist page', () => {
  it('shows the albums small, medium or large, and remembers it', async () => {
    renderArtist()
    const cover = async () => (await screen.findByRole('link', { name: /OK Computer/ })).querySelector('img')!.getAttribute('src')
    expect(await cover()).toContain('size=256')
    fireEvent.click(screen.getByRole('button', { name: 'L' }))
    expect(await cover()).toContain('size=512')
    expect(localStorage.getItem('artist-cover-size')).toBe('large')
  })
})
