import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AlbumPage } from './LibraryPage'

const summary = (key: number, title: string, year: number, release_type = 'Album') => ({
  rating_key: key, title, year, thumb: null, user_rating: null, plays: 0, popularity: null, critic_rating: null,
  track_count: 10, release_type, lyrics: null,
})
// As the artist page lists them: albums oldest first, then EPs (an EP older than all the albums too).
const albums = [summary(200, 'Pablo Honey', 1993), summary(201, 'The Bends', 1995), summary(205, 'Drill', 1992, 'EP')]
const listens = { listens: 0, first_at: null, last_at: null, years: [], songs: [], artist_keys: [], album_key: null }

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      const album = url.pathname.match(/^\/api\/servers\/1\/library\/albums\/(\d+)$/)
      if (album) {
        const al = albums.find((a) => a.rating_key === Number(album[1]))!
        return json({ ...al, summary: null, released: null, studio: null, artist_rating_key: 100, artist_title: 'Radiohead', tags: [], tracks: [], your_listens: listens })
      }
      if (url.pathname === '/api/servers/1/library/artists/100')
        return json({ ...summary(100, 'Radiohead', 0), album_count: 3, summary: null, art: null, country: null, tags: [], similar: [], albums, top_tracks: [], your_listens: listens })
      if (url.pathname === '/api/servers') return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: false, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      if (url.pathname === '/api/servers/1/sync') return json({ active: null, last: null, last_sync_at: null, counts: { artists: 1, albums: 3, tracks: 30, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
      return json([])
    }),
  )
  // A 1200 px wide page in a 1600 px window: room for the arrows.
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1600 })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const main = this.tagName === 'MAIN'
    const left = main ? 200 : 0
    const w = main ? 1200 : 0
    return { left, right: left + w, width: w, top: 0, bottom: 0, height: 0, x: left, y: 0, toJSON: () => ({}) }
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it('steps through the artist’s releases in the order their page shows them', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/library/albums/201']}>
        <main>
          <Routes>
            <Route path="/library/albums/:key" element={<AlbumPage />} />
          </Routes>
        </main>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(await screen.findByRole('heading', { name: 'The Bends' })).toBeInTheDocument()
  expect(await screen.findByRole('link', { name: 'Previous album: Pablo Honey' })).toHaveAttribute('href', '/library/albums/200')
  // After the albums, the EPs.
  const next = screen.getByRole('link', { name: 'Next album: Drill' })
  fireEvent.click(next)
  expect(await screen.findByRole('heading', { name: 'Drill' })).toBeInTheDocument()
  expect(await screen.findByRole('link', { name: 'Previous album: The Bends' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /^Next album/ })).not.toBeInTheDocument() // the last one
})
