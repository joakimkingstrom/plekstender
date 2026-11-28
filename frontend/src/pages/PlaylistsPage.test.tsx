import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PlaylistEditorPage, PlaylistsPage } from './PlaylistsPage'

const radiohead = { rating_key: 100, title: 'Radiohead', thumb: null }
const portishead = { rating_key: 101, title: 'Portishead', thumb: null }
const gloryBox = { rating_key: 304, title: 'Glory Box', artist: 'Portishead', thumb: null }
const song = (key: number, title: string) => ({
  rating_key: key,
  title,
  artist: 'Radiohead',
  artist_key: 100,
  album: 'OK Computer',
  thumb: null,
  duration_ms: 240_000,
  rating: null,
})

let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>
let playlists: unknown[] = []

beforeEach(() => {
  playlists = []
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    const url = new URL(input.url)
    const path = url.pathname
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    if (path === '/api/servers')
      return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
    if (path === '/api/servers/1/sync')
      return json({ active: null, last: null, last_sync_at: null, counts: { artists: 2, albums: 1, tracks: 4, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
    if (path === '/api/servers/1/library/tags') return json([])
    if (path === '/api/servers/1/playlists/search') return json({ artists: [radiohead], tracks: [gloryBox] })
    if (path === '/api/servers/1/playlists/suggestions')
      return json({ similar: [portishead], sonic_artists: [], sonic_tracks: [] })
    if (path === '/api/servers/1/playlists/preview') {
      const body = await input.clone().json()
      if (body.new_albums && body.artists.length === 0) {
        const songs = [{ ...song(302, 'Karma Police (Remastered)'), album: 'OK Computer OKNOTOK' }]
        return json({ matched: 1, count: 1, duration_ms: 240_000, artists: 1, top_artists: ['Radiohead'], songs })
      }
      if (body.forgotten && body.artists.length === 0) {
        const songs = [{ ...song(304, 'Glory Box'), artist: 'Portishead', artist_key: 101 }]
        return json({ matched: 1, count: 1, duration_ms: 240_000, artists: 1, top_artists: ['Portishead'], songs })
      }
      const songs = [song(301, 'Karma Police'), song(300, 'Paranoid Android')]
      if (body.artists.includes(101)) songs.push({ ...song(304, 'Glory Box'), artist: 'Portishead', artist_key: 101 })
      return json({ matched: songs.length, count: songs.length, duration_ms: songs.length * 240_000, artists: 1, top_artists: ['Radiohead'], songs })
    }
    if (path === '/api/servers/1/playlists' && input.method === 'POST') {
      const body = await input.clone().json()
      return json({ id: 7, ...body, status: 'queued' }, 201)
    }
    if (path === '/api/servers/1/playlists') return json(playlists)
    return json({ detail: 'Not found' }, 404)
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
        <Routes>
          <Route path="/playlists" element={<PlaylistsPage />} />
          <Route path="/playlists/:id" element={<PlaylistEditorPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function posted(path: string) {
  const calls = fetchMock.mock.calls.filter(([r]) => new URL(r.url).pathname === path && r.method === 'POST')
  return Promise.all(calls.map(([r]) => r.clone().json()))
}

describe('Playlists', () => {
  it('explains what playlists are when there are none', async () => {
    renderAt('/playlists')
    expect(await screen.findByText('No playlists yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '+ New playlist' })).toHaveAttribute('href', '/library/playlists/new')
  })

  it("shows a gig playlist's setlist songs that aren't in the library", async () => {
    playlists = [
      {
        id: 7,
        name: 'Before the gig: Radiohead, 7 Dec 2026',
        description: null,
        definition: { tracks: [303, 300, 301], order: 'listed' },
        display: {
          gig: { kind: 'before', date: '2026-12-07' },
          setlist: {
            songs: 5,
            missing: [
              { artist: 'Radiohead', title: 'Creep' },
              { artist: 'Radiohead', title: 'Exit Music' },
            ],
            missing_count: 2,
          },
        },
        schedule: 'manual',
        plex_playlist_key: 9,
        status: 'ok',
        error: null,
        last_result: { count: 3, duration_ms: 720_000 },
        last_built_at: null,
        exportable: false,
      },
    ]
    renderAt('/playlists')
    const summary = await screen.findByText("2 of the setlists' 5 songs aren't in your library")
    fireEvent.click(summary)
    expect(screen.getByText('Creep')).toBeInTheDocument()
    expect(screen.getByText('Exit Music')).toBeInTheDocument()
  })

  it('builds a playlist from search and suggestions, previews it and saves it', async () => {
    renderAt('/playlists/new')
    fireEvent.change(await screen.findByRole('searchbox', { name: 'Search artists and songs' }), {
      target: { value: 'rad' },
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Add Radiohead' }))
    expect(await screen.findByRole('button', { name: 'Remove Radiohead' })).toBeInTheDocument()

    // Suggestions for the chosen artist, and a live preview.
    fireEvent.click(await screen.findByRole('button', { name: 'Add Portishead' }))
    expect(await screen.findByRole('button', { name: 'Remove Portishead' })).toBeInTheDocument()
    expect(await screen.findByText('3 songs')).toBeInTheDocument()
    expect(screen.getAllByText('Glory Box')).toHaveLength(2) // a search result, and in the preview

    const save = screen.getByRole('button', { name: 'Create in Plex' })
    expect(save).toBeDisabled() // needs a name
    fireEvent.change(screen.getByPlaceholderText('e.g. Trip hop essentials'), { target: { value: 'Bristol & Oxford' } })
    fireEvent.click(save)
    await vi.waitFor(async () =>
      expect(await posted('/api/servers/1/playlists')).toEqual([
        {
          name: 'Bristol & Oxford',
          description: null,
          schedule: 'manual',
          definition: {
            artists: [100, 101],
            tracks: [],
            forgotten: false,
            new_albums: null,
            rules: { min_rating: null, include_unrated: true, top_percent: null, op: 'AND', tags: [], covers: 'any', lyrics: 'any' },
            limit: 100,
            order: 'random',
          },
          display: { genres: [], tags: [] },
        },
      ]),
    )
  })

  it('starts a forgotten favourites playlist from History → Rediscover', async () => {
    renderAt('/playlists/new?forgotten=1')
    expect(await screen.findByDisplayValue('Forgotten favourites')).toBeInTheDocument()
    const forgotten = screen.getByRole('checkbox', { name: /Your forgotten favourites/ })
    expect(forgotten).toBeChecked()
    expect(await screen.findByText('Glory Box')).toBeInTheDocument() // the preview
    // Without it, and nothing else chosen, there's nothing to save.
    fireEvent.click(forgotten)
    expect(screen.getByRole('button', { name: 'Create in Plex' })).toBeDisabled()
    fireEvent.click(forgotten)
    fireEvent.click(screen.getByRole('button', { name: 'Create in Plex' }))
    await vi.waitFor(async () => expect(await posted('/api/servers/1/playlists')).toHaveLength(1))
    const [saved] = await posted('/api/servers/1/playlists')
    expect(saved).toMatchObject({ name: 'Forgotten favourites', schedule: 'weekly', definition: { forgotten: true, artists: [], tracks: [] } })
  })

  it('makes Fresh picks: the best of new albums, refreshed weekly', async () => {
    renderAt('/playlists/new')
    const fresh = await screen.findByRole('checkbox', { name: 'The best of new albums' })
    fireEvent.click(fresh)
    // Named for you (and kept if you'd named it), refreshed weekly.
    expect(await screen.findByDisplayValue('Fresh picks')).toBeInTheDocument()
    expect(await screen.findByText('Karma Police (Remastered)')).toBeInTheDocument() // the preview
    fireEvent.click(screen.getByRole('button', { name: /Songs from each album/ }))
    fireEvent.click(await screen.findByRole('option', { name: '5 songs' }))
    fireEvent.click(screen.getByRole('button', { name: 'Create in Plex' }))
    await vi.waitFor(async () => expect(await posted('/api/servers/1/playlists')).toHaveLength(1))
    const [saved] = await posted('/api/servers/1/playlists')
    expect(saved).toMatchObject({
      name: 'Fresh picks',
      schedule: 'weekly',
      definition: { new_albums: { days: 30, per_album: 5 }, artists: [], tracks: [] },
    })
  })

  it('keeps only covers, or only songs with lyrics of a kind', async () => {
    renderAt('/playlists/new')
    fireEvent.click(await screen.findByRole('checkbox', { name: 'The best of new albums' }))
    fireEvent.click(screen.getByRole('button', { name: /^Covers/ }))
    fireEvent.click(await screen.findByRole('option', { name: 'Only covers' }))
    fireEvent.click(screen.getByRole('button', { name: /^Lyrics/ }))
    fireEvent.click(await screen.findByRole('option', { name: 'Only synced lyrics' }))
    // The preview asks with them.
    await vi.waitFor(async () => expect((await posted('/api/servers/1/playlists/preview')).at(-1)).toMatchObject({ rules: { covers: 'only', lyrics: 'synced' } }))
    fireEvent.click(screen.getByRole('button', { name: 'Create in Plex' }))
    await vi.waitFor(async () => expect(await posted('/api/servers/1/playlists')).toHaveLength(1))
    const [saved] = await posted('/api/servers/1/playlists')
    expect(saved.definition.rules).toMatchObject({ covers: 'only', lyrics: 'synced' })
  })
})
