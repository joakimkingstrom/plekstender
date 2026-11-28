import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LibraryPage } from './LibraryPage'

const album = (key: number, title: string, artist: string, year: number, release_type = 'Album') => ({
  rating_key: key, title, year, thumb: null, popularity: null, critic_rating: null, track_count: 10,
  user_rating: null, plays: 0, release_type, video: null, lyrics: null, artist_rating_key: key - 100, artist_title: artist,
})
const ALL = [album(202, 'Dummy', 'Portishead', 1994, 'EP'), album(200, 'OK Computer', 'Radiohead', 1997), album(201, 'OKNOTOK', 'Radiohead', 2017, 'Compilation')]
let asked: URL[] = []

beforeEach(() => {
  asked = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/servers/1/library/albums') {
        asked.push(url)
        const type = url.searchParams.get('release_type')
        const items = ALL.filter((a) => !type || a.release_type === type)
        return json({ items, total: items.length, offset: 0, limit: 60, types: { Album: 1, EP: 1, Compilation: 1 } })
      }
      if (url.pathname === '/api/servers/1/library/artists') return json({ items: [], total: 0, offset: 0, limit: 60 })
      if (url.pathname === '/api/servers') return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: false, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      if (url.pathname === '/api/servers/1/sync') return json({ active: null, last: null, last_sync_at: '2026-11-28T10:00:00Z', counts: { artists: 2, albums: 3, tracks: 30, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
      return json([])
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderLibrary(at: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route path="/library" element={<LibraryPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

it('lists every album with its artist, and filters them by release type', async () => {
  renderLibrary('/library')
  fireEvent.click(await screen.findByRole('button', { name: 'Albums' }))
  const grid = await screen.findByRole('list', { name: 'Albums' })
  expect(within(grid).getAllByRole('listitem').map((li) => li.textContent?.split('Rate')[0])).toEqual([
    // (The cover's placeholder letter first: there's no cover.)
    'DDummyPortishead · 1994 · EP',
    'OOK ComputerRadiohead · 1997',
    'OOKNOTOKRadiohead · 2017 · Compilation',
  ])
  expect(within(grid).getAllByRole('link', { name: 'Radiohead' })[0]).toHaveAttribute('href', '/library/artists/100')
  // The release types there are, named for the filter.
  const types = screen.getByRole('group', { name: 'Release type' })
  expect(within(types).getAllByRole('button').map((b) => b.textContent)).toEqual(['All 3', 'Albums 1', 'Compilations 1', 'EPs 1'])
  fireEvent.click(within(types).getByRole('button', { name: /Compilations/ }))
  await vi.waitFor(() => expect(asked.at(-1)?.searchParams.get('release_type')).toBe('Compilation'))
  expect(await screen.findByText('1 album')).toBeInTheDocument()
  // Sorted by artist to start with.
  expect(asked[0].searchParams.get('sort')).toBe('artist')
})
