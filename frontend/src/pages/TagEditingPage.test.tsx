import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TagEditingPage } from './TagEditingPage'

const album = (key: number, title: string, extra = {}) => ({
  rating_key: key, title, year: 1997, thumb: null, artist_rating_key: 100, artist_title: 'Radiohead',
  files: 12, differs: 0, differing_fields: [], no_mbid: 12, missing: 0, errors: 0, ...extra,
})
const release = (id: string, title: string, score: number, tracks: number) => ({
  id, score, title, artist: 'Radiohead', date: '1997-05-21', country: 'GB', formats: 'CD', track_count: tracks,
  label: 'Parlophone', catalog_number: null, type: 'Album', status: 'Official', disambiguation: null, barcode: null,
})
const best: Record<number, unknown> = {
  200: { release: release('11111111-1111-1111-1111-111111111111', 'OK Computer', 100, 12), source: 'search', files: 12, matched: 12, changes: 40, clear: true, note: null },
  201: { release: release('22222222-2222-2222-2222-222222222222', 'Kid A', 85, 11), source: 'search', files: 12, matched: 11, changes: 30, clear: false, note: '1 of 12 files didn’t match a track.' },
}
let fetchMock: ReturnType<typeof vi.fn>
let kidASkipped = false

beforeEach(() => {
  kidASkipped = false
  localStorage.removeItem('tag-editing-hide-skipped')
  fetchMock = vi.fn(async (input: Request) => {
    const url = new URL(input.url)
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    switch (url.pathname) {
      case '/api/auth/me':
        return json({ id: 1, username: 'me', email: null, thumb_url: null, is_admin: true })
      case '/api/servers':
        return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      case '/api/servers/1/sync':
        return json({ active: null, last: null, last_sync_at: null, counts: { artists: 1, albums: 2, tracks: 24, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
      case '/api/servers/1/files/summary':
        return json({ mapped: true, tag_editing: true, files: 24, matched: 24, errors: 0, active: null, last: null })
      case '/api/servers/1/files/albums': {
        // More than the page shows: The Bends is beyond it (asked for by "Next 50 albums").
        const hiding = url.searchParams.get('skipped_within') !== '0'
        const items = [album(200, 'OK Computer', { differs: 2, differing_fields: ['title', 'year'] })]
        if (!(hiding && kidASkipped)) items.push(album(201, 'Kid A', { skipped_at: kidASkipped && !hiding ? new Date(Date.now() - 3 * 86_400_000).toISOString() : null }))
        if (url.searchParams.get('limit') === '200') items.push(album(202, 'The Bends'))
        return json({
          items,
          total: items.length, offset: 0, limit: 50, albums: 5, counts: { any: 2, differs: 1, no_mbid: 2, missing: 0, errors: 0 }, not_in_plex: 3,
          skipped_hidden: hiding && kidASkipped ? 1 : 0,
        })
      }
      case '/api/servers/1/files/albums/skipped':
        kidASkipped = true
        return new Response(null, { status: 204 })
      case '/api/servers/1/files/albums/skipped/201':
        kidASkipped = false
        return new Response(null, { status: 204 })
      case '/api/servers/1/files/albums/202/musicbrainz/best':
        return json(best[201])
      case '/api/servers/1/files/albums/200/musicbrainz/best':
        return json(best[200])
      case '/api/servers/1/files/albums/201/musicbrainz/best':
        return json(best[201])
      case '/api/servers/1/files/albums/200/musicbrainz':
        return json({ id: 9, kind: 'edit', description: 'x', status: 'queued', error: null, user: 'me', created_at: new Date().toISOString(), applied_at: null, total: 40, counts: {}, undoable: 0, changes: [] }, 201)
      default:
        return json({ detail: 'Not found' }, 404)
    }
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Tag editing', () => {
  it('lists albums that need a look and accepts clear MusicBrainz matches', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/library/tag-editing']}>
          <Routes>
            <Route path="/library/tag-editing" element={<TagEditingPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('link', { name: 'OK Computer' })).toHaveAttribute('href', '/library/albums/200')
    expect(screen.getByText('2 differ from Plex: title, year')).toBeInTheDocument()
    expect(screen.getAllByText('No MusicBrainz ID')).toHaveLength(2)
    expect(screen.getByText(/3 files aren't in Plex/)).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Select all on this page'))
    expect(screen.getByText('2 albums selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Look up on MusicBrainz' }))
    const dialog = await screen.findByRole('dialog', { name: 'MusicBrainz releases' })
    expect(await within(dialog).findByText('Clear match')).toBeInTheDocument()
    expect(await within(dialog).findByText('1 of 12 files didn’t match a track.')).toBeInTheDocument()

    // Only the clear match is accepted together; Kid A waits for a look.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Accept 1 clear match' }))
    expect(await within(dialog).findByText('Queued for writing')).toBeInTheDocument()
    const applied = fetchMock.mock.calls.map(([r]) => new URL((r as Request).url).pathname).filter((p) => p.endsWith('/musicbrainz'))
    expect(applied).toEqual(['/api/servers/1/files/albums/200/musicbrainz'])
    const post = fetchMock.mock.calls.find(([r]) => new URL((r as Request).url).pathname.endsWith('/200/musicbrainz'))!
    expect(await (post[0] as Request).clone().json()).toEqual({ release_id: '11111111-1111-1111-1111-111111111111' })

    // The next albums are offered: those not in this dialog (or fixed), so The Bends.
    expect(within(dialog).getByText('1 album queued for writing.')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Next 50 albums' }))
    expect(await screen.findByRole('link', { name: /The Bends/ })).toBeInTheDocument()
    const next = screen.getByRole('dialog', { name: 'MusicBrainz releases' })
    expect(within(next).queryByRole('link', { name: /^Kid A · / })).not.toBeInTheDocument() // the album (not a release)

    // Closed: the album just fixed is hidden while its files are written.
    fireEvent.click(within(next).getByRole('button', { name: /Close/ }))
    expect(await screen.findByText(/1 just fixed, hidden/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'OK Computer' })).not.toBeInTheDocument()
  })

  it('skips the rest of a batch, hides skipped albums for a while, and takes a skip back', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/library/tag-editing']}>
          <Routes>
            <Route path="/library/tag-editing" element={<TagEditingPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    // Hiding skipped albums is on unless turned off.
    expect(await screen.findByRole('checkbox', { name: /Hide albums skipped in the last 90 days/ })).toBeChecked()
    const asked = () => fetchMock.mock.calls.map(([r]) => new URL((r as Request).url)).filter((u) => u.pathname === '/api/servers/1/files/albums')
    expect(asked()[0].searchParams.get('skipped_within')).toBe('90')

    fireEvent.click(screen.getByLabelText('Select all on this page'))
    fireEvent.click(screen.getByRole('button', { name: 'Look up on MusicBrainz' }))
    const dialog = await screen.findByRole('dialog', { name: 'MusicBrainz releases' })
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Accept 1 clear match' }))
    // Accepted OK Computer; Kid A is the rest.
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Skip the rest (1)' }))
    expect(await within(dialog).findByText('1 album queued for writing. 1 skipped.')).toBeInTheDocument()
    const skip = fetchMock.mock.calls.find(([r]) => new URL((r as Request).url).pathname === '/api/servers/1/files/albums/skipped')!
    expect(await (skip[0] as Request).clone().json()).toEqual({ album_keys: [201] })
    fireEvent.click(within(dialog).getByRole('button', { name: /Close/ }))
    expect(await screen.findByText(/\(1 hidden\)/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Kid A' })).not.toBeInTheDocument()

    // Shown again: when it was skipped, and a way to take that back.
    fireEvent.click(screen.getByRole('checkbox', { name: /Hide albums skipped/ }))
    expect(await screen.findByText(/Skipped 3 days ago/)).toBeInTheDocument()
    expect(localStorage.getItem('tag-editing-hide-skipped')).toBe('off')
    fireEvent.click(screen.getByRole('button', { name: 'Stop skipping Kid A' }))
    await vi.waitFor(() => expect(screen.queryByText(/Skipped 3 days ago/)).not.toBeInTheDocument())
  })
})
