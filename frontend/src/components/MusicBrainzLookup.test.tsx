import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MusicBrainzLookup } from './MusicBrainzLookup'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const RELEASE = {
  id: '0b6b4ba0-d36f-47bd-b4ea-6a5b91842d29', score: 100, title: 'OK Computer', artist: 'Radiohead', date: '1997-05-21',
  country: 'GB', formats: 'CD', track_count: 2, label: 'Parlophone', catalog_number: 'NODATA 02', type: 'Album',
  status: 'Official', disambiguation: null, barcode: null,
}
const tracks = [
  { id: 't1', disc: 1, position: 1, title: 'Airbag', artist: 'Radiohead', length_ms: 284_000 },
  { id: 't2', disc: 1, position: 2, title: 'Paranoid Android', artist: 'Radiohead', length_ms: 383_000 },
]
const file = (id: number, title: string, track: number, trackId: string | null, changes: unknown[]) => ({
  file_id: id, path: `OK Computer/${track}.flac`, disc: 1, track, title, duration_ms: 383_000, track_id: trackId, changes,
})

describe('MusicBrainz lookup', () => {
  it('searches, matches files to tracks, and writes the release', async () => {
    const previews: unknown[] = []
    const fetch = vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      if (url.pathname.endsWith('/musicbrainz/search')) return json([RELEASE])
      if (url.pathname.endsWith('/musicbrainz/preview')) {
        const body = await input.clone().json()
        previews.push(body)
        const picked = body.mapping?.['11'] ?? 't2'
        return json({
          release: RELEASE,
          tracks,
          files: [
            file(11, 'Paranoid Android (Live)', 2, picked, [
              { field: 'title', old_value: 'Paranoid Android (Live)', new_value: picked === 't2' ? 'Paranoid Android' : 'Airbag' },
              { field: 'musicbrainz_albumid', old_value: null, new_value: RELEASE.id },
            ]),
          ],
          changes: 2,
        })
      }
      if (url.pathname.endsWith('/albums/200/musicbrainz')) return json({ id: 9, status: 'queued' }, 201)
      if (url.pathname.endsWith('/musicbrainz/suggested')) return json([])
      if (url.pathname.endsWith('/files/identify')) return json({ configured: false, active: null, last: null })
      return json({ detail: 'Not found' }, 404)
    })
    vi.stubGlobal('fetch', fetch)
    const onClose = vi.fn()
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MusicBrainzLookup serverId={1} albumKey={200} album="OK Computer" artist="Radiohead" fileCount={2} canWrite onClose={onClose} />
      </QueryClientProvider>,
    )
    // Searched straight away with the album and artist.
    const releases = await screen.findByRole('list', { name: 'Releases' })
    const search = new URL(fetch.mock.calls.find(([r]) => r.url.includes('/musicbrainz/search'))![0].url)
    expect([search.searchParams.get('album'), search.searchParams.get('artist')]).toEqual(['OK Computer', 'Radiohead'])
    expect(within(releases).getByText('2 tracks, like your files')).toBeInTheDocument()
    expect(within(releases).getByText(/1997-05-21 · GB · CD · 2 tracks · Parlophone NODATA 02 · Album/)).toBeInTheDocument()
    fireEvent.click(within(releases).getByRole('button'))

    // Matched automatically; the changes per file, MusicBrainz IDs summed up.
    const select = (await screen.findByRole('combobox', { name: 'Release track for Paranoid Android (Live)' })) as HTMLSelectElement
    expect(select.value).toBe('t2')
    expect(screen.getByText('Paranoid Android')).toBeInTheDocument()
    expect(screen.getByText('+ 1 MusicBrainz ID')).toBeInTheDocument()
    expect(previews[0]).toMatchObject({ release_id: RELEASE.id, mapping: null })

    // Another track for the file: previewed with that match.
    fireEvent.change(select, { target: { value: 't1' } })
    expect(await screen.findByText('Airbag')).toBeInTheDocument()
    expect(previews.at(-1)).toMatchObject({ mapping: { '11': 't1' } })
    expect(screen.getByText(/The file is 99 s longer than this track/)).toBeInTheDocument()

    // Without the IDs (one less kind of tag), then write.
    fireEvent.click(screen.getByRole('checkbox', { name: 'MusicBrainz IDs' }))
    await vi.waitFor(() => expect((previews.at(-1) as { fields: string[] }).fields).not.toContain('musicbrainz_albumid'))
    fireEvent.click(await screen.findByRole('button', { name: 'Write 2 changes' }))
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled())
    const write = fetch.mock.calls.find(([r]) => new URL(r.url).pathname.endsWith('/albums/200/musicbrainz'))
    expect(await write![0].clone().json()).toMatchObject({ release_id: RELEASE.id, mapping: { '11': 't1' } })
  })

  it('suggests the releases the files sound like, or offers to identify them', async () => {
    let suggested: unknown[] = []
    const fetch = vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      if (url.pathname.endsWith('/musicbrainz/search')) return json([])
      if (url.pathname.endsWith('/musicbrainz/suggested')) return json(suggested)
      if (url.pathname.endsWith('/files/identify') && input.method === 'POST') {
        suggested = [{ ...RELEASE, score: 100 }]
        return json({ id: 3, status: 'queued', progress: {}, stats: {}, error: null, created_at: '', finished_at: null }, 201)
      }
      if (url.pathname.endsWith('/files/identify')) return json({ configured: true, active: null, last: null })
      return json({ detail: 'Not found' }, 404)
    })
    vi.stubGlobal('fetch', fetch)
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MusicBrainzLookup serverId={1} albumKey={200} album="OK Computer" artist="Radiohead" fileCount={2} canWrite onClose={() => {}} />
      </QueryClientProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Identify these files by sound' }))
    await vi.waitFor(async () => {
      const post = fetch.mock.calls.find(([r]) => r.method === 'POST')
      expect(await post![0].clone().json()).toEqual({ album_key: 200, again: false })
    })
  })
})
