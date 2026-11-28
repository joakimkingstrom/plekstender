import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
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
const highAndDry = {
  rating_key: 2011, title: 'High and Dry', original_title: null, disc: 1, number: 2, duration_ms: 257_000, popularity: 900,
  user_rating: null, plays: 4, album_rating_key: 201, album_title: 'The Bends', album_thumb: null, listens: 0, seen_live: 0,
  lyrics: null,
}
const version = (key: number, title: string, album: number, album_title: string, year: number, release_type: string, current: boolean) => ({
  rating_key: key, title, album_rating_key: album, album_title, album_thumb: null, year, release_type, duration_ms: 257_000,
  user_rating: null, plays: current ? 4 : 0, current,
})

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      const album = url.pathname.match(/^\/api\/servers\/1\/library\/albums\/(\d+)$/)
      if (album) {
        if (album[1] === '209')
          return json({
            ...summary(209, 'I Might Be Wrong', 2001, 'Live'), summary: null, released: null, studio: null, artist_rating_key: 100,
            artist_title: 'Radiohead', tags: [], tracks: [], your_listens: listens, sources_unmatched: 2,
            song_sources: [
              { rating_key: 205, title: 'Drill', artist: 'Radiohead', year: 1992, thumb: null, release_type: 'EP', songs: 1, tracks: [{ rating_key: 2093, title: 'Thinking About You (Live)', disc: 1, number: 3 }] },
              { rating_key: 201, title: 'The Bends', artist: 'Radiohead', year: 1995, thumb: null, release_type: 'Album', songs: 4, tracks: [] },
            ],
          })
        if (album[1] === '210')
          return json({
            ...summary(210, 'Trip-Hop Classics', 2005, 'Compilation'), summary: null, released: null, studio: null, artist_rating_key: 104,
            artist_title: 'Various Artists', tags: [], tracks: [], your_listens: listens, sources_unmatched: 1,
            song_sources: [
              { rating_key: 300, title: 'Dummy', artist: 'Portishead', year: 1994, thumb: null, release_type: 'Album', songs: 2, tracks: [{ rating_key: 2101, title: 'Glory Box', disc: 1, number: 1 }, { rating_key: 2104, title: 'Sour Times', disc: 1, number: 4 }] },
              { rating_key: 301, title: 'Mezzanine', artist: 'Massive Attack', year: 1998, thumb: null, release_type: 'Album', songs: 1, tracks: [{ rating_key: 2102, title: 'Teardrop', disc: 1, number: 2 }] },
            ],
          })
        const al = albums.find((a) => a.rating_key === Number(album[1]))!
        const tracks = al.rating_key === 201 ? [highAndDry] : []
        return json({ ...al, summary: null, released: null, studio: null, artist_rating_key: 100, artist_title: 'Radiohead', tags: [], tracks, your_listens: listens })
      }
      if (url.pathname === '/api/servers/1/library/tracks/2011/versions')
        return json({
          title: 'High and Dry', artist: 'Radiohead',
          versions: [
            version(2011, 'High and Dry', 201, 'The Bends', 1995, 'Album', true),
            version(2091, 'High and Dry (Live)', 209, 'I Might Be Wrong', 2001, 'Live', false),
          ],
        })
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

it('lists every version of a song when its title is clicked', async () => {
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
  fireEvent.click(await screen.findByRole('button', { name: 'High and Dry' }))
  const dialog = await screen.findByRole('dialog')
  expect(await within(dialog).findByText('Radiohead · 2 versions')).toBeInTheDocument()
  expect(await within(dialog).findByText('High and Dry (Live)')).toBeInTheDocument()
  expect(within(dialog).getByRole('link', { name: 'I Might Be Wrong' })).toHaveAttribute('href', '/library/albums/209')
  expect(within(dialog).getByText(/2001 · Live/)).toBeInTheDocument()
  expect(within(dialog).getByText('(this one)')).toBeInTheDocument()
  expect(within(dialog).getAllByRole('button', { name: /Play a preview of/ })).toHaveLength(2)
})

it('shows where a live album’s songs come from, linking to each release', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/library/albums/209']}>
        <main>
          <Routes>
            <Route path="/library/albums/:key" element={<AlbumPage />} />
          </Routes>
        </main>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const list = await screen.findByRole('list', { name: 'Songs by release' })
  const releases = within(list).getAllByRole('button')
  expect(releases.map((b) => b.getAttribute('aria-label'))).toEqual(['1 song from Drill (1992)', '4 songs from The Bends (1995)'])
  // The arrow beside each goes to the release.
  expect(within(list).getByRole('link', { name: 'Go to The Bends' })).toHaveAttribute('href', '/library/albums/201')
  expect(screen.getByText(/2 of 7 songs aren’t on one of the artist’s studio releases/)).toBeInTheDocument()
})

it('shows where a compilation’s songs come from, each release artist first', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/library/albums/210']}>
        <main>
          <Routes>
            <Route path="/library/albums/:key" element={<AlbumPage />} />
          </Routes>
        </main>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const list = await screen.findByRole('list', { name: 'Songs by release' })
  const releases = within(list).getAllByRole('button')
  expect(releases.map((b) => b.getAttribute('aria-label'))).toEqual([
    '2 songs from Portishead · Dummy (1994)',
    '1 song from Massive Attack · Mezzanine (1998)',
  ])
  expect(within(list).getByRole('link', { name: 'Go to Portishead · Dummy' })).toHaveAttribute('href', '/library/albums/300')
  // Clicking a release lists its songs, as this album has them; clicking again hides them.
  expect(screen.queryByRole('list', { name: 'Songs from Portishead · Dummy' })).not.toBeInTheDocument()
  fireEvent.click(releases[0])
  expect(releases[0]).toHaveAttribute('aria-expanded', 'true')
  const songs = screen.getByRole('list', { name: 'Songs from Portishead · Dummy' })
  expect(within(songs).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['1Glory Box', '4Sour Times'])
  fireEvent.click(releases[0])
  expect(screen.queryByRole('list', { name: 'Songs from Portishead · Dummy' })).not.toBeInTheDocument()
  expect(screen.getByText(/1 of 4 songs aren’t on another album, EP, or single in the library/)).toBeInTheDocument()
})
