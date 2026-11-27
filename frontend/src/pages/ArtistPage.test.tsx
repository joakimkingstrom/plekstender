import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ArtistPage } from './LibraryPage'

const album = (key: number, title: string) => ({
  rating_key: key, title, year: 1997, thumb: `/library/metadata/${key}/thumb/1`, user_rating: null, plays: 0,
  popularity: null, release_type: 'Album', artist_rating_key: 100, artist_title: 'Radiohead',
})

// Portishead's top song is a cover (SecondHandSongs).
const gloryBox = {
  rating_key: 304, title: 'Glory Box', original_title: null, disc: 1, number: 11, duration_ms: 300_000,
  popularity: 800, user_rating: null, plays: 3, album_rating_key: 202, album_title: 'Dummy', album_thumb: null,
  listens: 0, seen_live: 0, lyrics: null,
  cover_of: { artist: 'Isaac Hayes', title: "Ike's Rap II", url: 'https://secondhandsongs.com/performance/20', cover_url: null },
}

const artist = (key: number, title: string) => ({
  rating_key: key, title, thumb: null, popularity: null, user_rating: null, plays: 5, album_count: 2,
  summary: null, art: null, country: null, tags: [], similar: [],
  albums: [album(200, 'OK Computer'), album(201, 'Kid A')], top_tracks: key === 99 ? [gloryBox] : [],
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
      const n = url.pathname.match(/^\/api\/servers\/1\/library\/artists\/(\d+)\/neighbours$/)
      if (n) {
        const order = [99, 100, 101]
        const i = order.indexOf(Number(n[1]))
        const at = (k: number | undefined) => (k ? { rating_key: k, title: { 99: 'Portishead', 100: 'Radiohead', 101: 'Sigur Rós' }[k]! } : null)
        return json({ previous: at(order[i - 1]), next: at(order[i + 1]) })
      }
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
  vi.restoreAllMocks()
})

/** The page's <main>: `width` wide in the middle of a `window`-wide window. */
function layout(window_: number, width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: window_ })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const left = this.tagName === 'MAIN' ? (window_ - width) / 2 : 0
    const w = this.tagName === 'MAIN' ? width : 0
    return { left, right: left + w, width: w, top: 0, bottom: 0, height: 0, x: left, y: 0, toJSON: () => ({}) }
  })
}

function renderArtist(key = 100) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[`/library/artists/${key}`]}>
        <main>
          <Routes>
            <Route path="/library/artists/:key" element={<ArtistPage />} />
          </Routes>
        </main>
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

  it('says whose song a cover is, artist first, linking to the original', async () => {
    renderArtist(99)
    const link = await screen.findByRole('link', { name: "Isaac Hayes · Ike's Rap II" })
    expect(link).toHaveAttribute('href', 'https://secondhandsongs.com/performance/20')
    expect(link.parentElement).toHaveTextContent("Cover of Isaac Hayes · Ike's Rap II")
  })

  it('steps to the previous and next artist by name', async () => {
    layout(1600, 1152) // 224 px either side
    renderArtist()
    const next = await screen.findByRole('link', { name: 'Next artist: Sigur Rós' })
    // In the margin, outside the content (which starts at 224 px).
    expect(next.style.right).toBe('168px')
    expect(screen.getByRole('link', { name: 'Previous artist: Portishead' }).style.left).toBe('168px')
    expect(screen.getByRole('link', { name: 'Previous artist: Portishead' })).toHaveAttribute('href', '/library/artists/99')
    fireEvent.click(next)
    expect(await screen.findByRole('heading', { name: 'Sigur Rós' })).toBeInTheDocument()
    // The last one: no next.
    expect(await screen.findByRole('link', { name: 'Previous artist: Radiohead' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /^Next artist/ })).not.toBeInTheDocument()
  })

  it('leaves the arrows out when the margins are too narrow for them', async () => {
    layout(1200, 1152) // 24 px either side
    renderArtist()
    expect(await screen.findByRole('heading', { name: 'Radiohead' })).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByRole('link', { name: /artist:/ })).not.toBeInTheDocument()
  })
})
