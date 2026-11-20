import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TagPage } from './TagPage'

const requested: string[] = []

beforeEach(() => {
  requested.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      requested.push(url.pathname + url.search)
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
      switch (url.pathname) {
        case '/api/auth/me':
          return json({ id: 1, username: 'me', email: null, thumb_url: null, is_admin: false })
        case '/api/servers':
          return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: false, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
        case '/api/servers/1/sync':
          return json({ active: null, last: null, last_sync_at: null, counts: { artists: 1, albums: 1, tracks: 2, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
        case '/api/servers/1/library/tags/7':
          return json({
            id: 7,
            kind: 'style',
            name: 'Art Rock',
            artist_count: 1,
            album_count: 1,
            artists: [{ rating_key: 100, title: 'Radiohead', thumb: null, popularity: null, user_rating: null, plays: 12, album_count: 2 }],
            albums: [
              {
                rating_key: 200, title: 'OK Computer', year: 1997, thumb: null, popularity: null, critic_rating: null,
                track_count: 12, user_rating: null, plays: 5, release_type: 'Album', artist_rating_key: 100, artist_title: 'Radiohead',
              },
            ],
            related: [
              { id: 3, kind: 'genre', name: 'Rock', count: 4 },
              { id: 9, kind: 'mood', name: 'Melancholy', count: 2 },
            ],
          })
        default:
          return json({ detail: 'Not found' }, 404)
      }
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Tag pages', () => {
  it('lists the artists and albums, related tags and a playlist shortcut', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/library/tags/7']}>
          <Routes>
            <Route path="/library/tags/:id" element={<TagPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('heading', { name: 'Art Rock' })).toBeInTheDocument()
    expect(screen.getByText('Style')).toBeInTheDocument()
    expect(screen.getByText('1 artist · 1 album')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Radiohead/ })).toHaveAttribute('href', '/library/artists/100')
    expect(screen.getByRole('link', { name: 'OK Computer' })).toHaveAttribute('href', '/library/albums/200')
    expect(screen.getByRole('link', { name: 'Genre: Rock' })).toHaveAttribute('href', '/library/tags/3')
    expect(screen.getByRole('link', { name: 'Mood: Melancholy' })).toHaveAttribute('href', '/library/tags/9')
    expect(screen.getByRole('link', { name: 'Make a playlist' })).toHaveAttribute(
      'href',
      '/library/playlists/new?tag=7&name=Art+Rock&kind=style',
    )
    expect(requested).toContain('/api/servers/1/library/tags/7?sort=plays')
  })
})
