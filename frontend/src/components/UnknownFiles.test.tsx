import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UnknownFiles } from './UnknownFiles'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const release = (id: string, title: string, date: string, trackId: string) => ({
  id, title, artist: 'Radiohead', date, country: 'GB', format: 'CD', disc: 1, discs: 1, track_count: 12, position: 1, track_id: trackId,
})
const demo = {
  file_id: 5, path: 'extra/demo.mp3', in_plex: false, duration_ms: 284_000, tags: { title: 'A demo' }, status: 'identified', error: null,
  results: [{ acoustid: 'a1', score: 0.93, recordings: [{ id: 'rec1', title: 'Airbag', artist: 'Radiohead', duration: 284, releases: [release('rel1', 'OK Computer', '1997-05-21', 't1'), release('rel2', 'OKNOTOK', '2017-06-23', 't2')] }] }],
  suggestion: { file_id: 5, recording_id: 'rec1', track_id: 't1' },
}
const unread = { ...demo, file_id: 6, path: 'extra/new.flac', status: 'new', results: [], suggestion: null }

describe('Unknown files', () => {
  it('identifies files, offers their matches and writes the chosen ones', async () => {
    const fetch = vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      if (url.pathname.endsWith('/files/unknown')) return json({ total: 2, files: [demo, unread] })
      if (url.pathname.endsWith('/files/identify') && input.method === 'POST')
        return json({ id: 3, status: 'queued', progress: {}, stats: {}, error: null, created_at: '', finished_at: null }, 201)
      if (url.pathname.endsWith('/files/identify'))
        return json({ configured: true, active: null, last: { id: 2, status: 'succeeded', progress: {}, stats: { identified: 1, no_match: 0, errors: 0 }, error: null, created_at: '', finished_at: null } })
      if (url.pathname.endsWith('/identify/preview'))
        return json({ files: [{ file_id: 5, path: 'extra/demo.mp3', changes: [{ field: 'title', old_value: 'A demo', new_value: 'Airbag' }, { field: 'musicbrainz_trackid', old_value: null, new_value: 'rec1' }] }], changes: 2 })
      if (url.pathname.endsWith('/identify/apply')) return json({ id: 9, status: 'queued' }, 201)
      return json({ detail: 'Not found' }, 404)
    })
    vi.stubGlobal('fetch', fetch)
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <UnknownFiles serverId={1} canWrite isAdmin />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    // The suggestion is picked; the other releases (and "just the song") can be.
    const match = await screen.findByRole('button', { name: 'Match for demo.mp3' })
    fireEvent.click(match)
    expect(screen.getByRole('option', { selected: true })).toHaveTextContent(/OK Computer \(1997/)
    expect(screen.getByRole('option', { name: 'Airbag · Radiohead · OKNOTOK (2017, GB, CD) · 93%' })).toBeInTheDocument()
    fireEvent.click(match) // close
    expect(screen.getByText('Not identified yet.')).toBeInTheDocument()
    expect(screen.getByText('Last run: 1 identified.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Identify them by sound' }))
    await vi.waitFor(async () => {
      const post = fetch.mock.calls.find(([r]) => r.method === 'POST' && r.url.endsWith('/files/identify'))
      expect(await post![0].clone().json()).toEqual({ kind: 'missing', again: false })
    })

    fireEvent.click(match)
    fireEvent.click(screen.getByRole('option', { name: 'Airbag · Radiohead · OKNOTOK (2017, GB, CD) · 93%' }))
    fireEvent.click(screen.getByRole('button', { name: 'Check the changes for 1 file' }))
    expect(await screen.findByText('Airbag')).toBeInTheDocument()
    expect(screen.getByText('+ 1 MusicBrainz ID')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Write 2 changes' }))
    await vi.waitFor(async () => {
      const post = fetch.mock.calls.find(([r]) => r.url.endsWith('/identify/apply'))
      expect(await post![0].clone().json()).toEqual({ choices: [{ file_id: 5, recording_id: 'rec1', track_id: 't2' }] })
    })
  })
})
