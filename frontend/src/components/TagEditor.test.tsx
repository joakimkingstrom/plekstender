import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AlbumFiles } from './MusicFiles'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const plex = (title: string, track: number) => ({ title, artist: 'Radiohead', album: 'OK Computer', albumartist: 'Radiohead', track, disc: 1, year: 1997, genre: ['Rock'] })
const tags = (title: string, track: number, extra = {}) => ({ title, artist: 'Radiohead', album: 'OK Computer', albumartist: 'Radiohead', tracknumber: String(track), track, date: '1997', genre: ['Rock'], ...extra })
const row = (key: number, fileId: number, title: string, track: number, extra = {}) => ({
  rating_key: key, file_id: fileId, disc: 1, number: track, plex: plex(title, track), file: `Radiohead/OK Computer/${track}.flac`,
  format: 'flac', duration_ms: 200_000, has_cover: true, tags: tags(title, track, extra), error: null, differences: [],
})
const history = [
  {
    id: 7, kind: 'edit', description: 'Fixed the album artist', status: 'applied', error: null, user: 'joakim',
    created_at: new Date().toISOString(), applied_at: new Date().toISOString(),
    total: 2, counts: { applied: 1, conflict: 1 }, undoable: 1,
    changes: [
      { id: 70, file_id: 11, path: 'Radiohead/OK Computer/1.flac', rating_key: 300, field: 'albumartist', old_value: 'radiohead', new_value: 'Radiohead', status: 'applied', error: null, undoes_id: null, undone: false },
      { id: 71, file_id: 12, path: 'Radiohead/OK Computer/2.flac', rating_key: 301, field: 'albumartist', old_value: 'radiohead', new_value: 'Radiohead', status: 'conflict', error: 'changed elsewhere', undoes_id: null, undone: false },
    ],
  },
]

describe('Editing tags', () => {
  it('edits, reviews and writes; then undoes from the history', async () => {
    const fetch = vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/servers/1/files/albums/200')
        return json({ scanned: true, tracks: [row(300, 11, 'Airbag', 1), row(301, 12, 'Paranoid Android', 2, { artist: 'radiohead' })] })
      if (url.pathname === '/api/servers/1/files/summary')
        return json({ mapped: true, tag_editing: true, files: 2, matched: 2, errors: 0, active: null, last: null })
      if (url.pathname === '/api/servers/1/files/changes') return json(history)
      if (url.pathname === '/api/servers/1/files/edits' || url.pathname === '/api/servers/1/files/undo')
        return json({ ...history[0], id: 8, status: 'queued', changes: [] }, 201)
      return json({ detail: 'Not found' }, 404)
    })
    vi.stubGlobal('fetch', fetch)
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <AlbumFiles serverId={1} albumKey={200} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Edit tags' }))
    // Fix one song's artist and set the year for the whole album.
    fireEvent.change(screen.getByLabelText('Artist of Paranoid Android'), { target: { value: 'Radiohead' } })
    fireEvent.change(screen.getByLabelText(/^Year/), { target: { value: '1997-05-21' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review 3 changes' }))
    const dialog = await screen.findByRole('dialog', { name: 'Write these changes to the files?' })
    expect(within(dialog).getAllByText('1997-05-21')).toHaveLength(2)
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'Year and artist' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Write 3 changes' }))
    await vi.waitFor(async () => {
      const post = fetch.mock.calls.find(([r]) => new URL(r.url).pathname.endsWith('/files/edits'))
      expect(await post![0].clone().json()).toEqual({
        description: 'Year and artist',
        changes: [
          { file_id: 11, field: 'date', value: '1997-05-21' },
          { file_id: 12, field: 'date', value: '1997-05-21' },
          { file_id: 12, field: 'artist', value: 'Radiohead' },
        ],
      })
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    // The history: undo the written change (the skipped one can't be picked).
    const item = await screen.findByText('Fixed the album artist')
    fireEvent.click(item)
    // Changes grouped by file; only written ones can be picked.
    expect(screen.getAllByRole('checkbox', { name: /^Pick Album artist/ }).map((b) => b.getAttribute('aria-label'))).toEqual([
      'Pick Album artist of 1',
    ])
    expect(screen.getByRole('heading', { name: '2' })).toBeInTheDocument()
    expect(screen.getByText('left as it was')).toBeInTheDocument()
    expect(screen.getByText(/1 change was left as it was: the file held something other/)).toBeInTheDocument()
    expect(screen.getByText('1 written · 1 left as they were')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '↶ Undo this edit' }))
    await vi.waitFor(async () => {
      const post = fetch.mock.calls.find(([r]) => new URL(r.url).pathname.endsWith('/files/undo'))
      expect(await post![0].clone().json()).toEqual({ set_id: 7 })
    })
  })
})
