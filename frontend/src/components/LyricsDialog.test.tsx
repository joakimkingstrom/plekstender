import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LyricsDialog } from './LyricsDialog'

const job = { id: 5, status: 'succeeded', progress: { done: 3, total: 3 }, stats: {}, error: null, created_at: '2026-11-24T10:00:00Z', finished_at: '2026-11-24T10:01:00Z' }

let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>

beforeEach(() => {
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    const url = new URL(input.url)
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    if (url.pathname === '/api/servers/1/files/lyrics' && input.method === 'POST') return json({ ...job, status: 'queued' }, 201)
    if (url.pathname === '/api/servers/1/files/lyrics/5') {
      const both = url.searchParams.get('placement') === 'both'
      return json({
        job,
        files: 3, looked_up: 3, synced: 1, plain: 1, instrumental: 0, none: 1, untagged: 0, errors: 0, had_lyrics: 1,
        lrc: 1, txt: 1, lyrics: both ? 1 : 0, songs: 2,
        sample: [
          { path: 'Radiohead/OK Computer/02 Paranoid Android.flac', status: 'synced', lrc: true, txt: false, lyrics: both, had_lyrics: false, preview: 'Please could you stop the noise\nI am trying to get some rest' },
          { path: 'Radiohead/OK Computer/06 Karma Police.flac', status: 'plain', lrc: false, txt: true, lyrics: false, had_lyrics: true, preview: 'Karma police' },
        ],
        applied_set_id: null,
      })
    }
    if (url.pathname === '/api/servers/1/files/lyrics/5/apply')
      return json({ id: 9, kind: 'edit', description: 'Lyrics', status: 'queued', error: null, user: 'me', created_at: '2026-11-24T10:02:00Z', applied_at: null, total: 3, counts: { pending: 3 }, undoable: 0, changes: [] }, 201)
    return json({ detail: 'Not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const posted = (path: string) =>
  Promise.all(fetchMock.mock.calls.filter(([r]) => new URL(r.url).pathname === path && r.method === 'POST').map(([r]) => r.clone().json()))

it('looks up lyrics for the chosen albums, previews them and writes them as an edit', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <LyricsDialog serverId={1} albums={[200, 201]} canWrite onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(screen.getByText(/From LRCLIB, for 2 albums/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: 'Look again for songs looked up before' }))
  fireEvent.click(screen.getByRole('button', { name: 'Look up lyrics' }))
  expect(await screen.findByText('Writing changes 2 songs: 1 .lrc, 1 .txt.')).toBeInTheDocument()
  expect(await posted('/api/servers/1/files/lyrics')).toEqual([{ albums: [200, 201], again: true }])
  expect(screen.getByText(/Of 3 songs; 1 already had lyrics/)).toBeInTheDocument()
  expect(screen.getByText('.txt · replaces lyrics it had · Karma police')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Both' }))
  expect(await screen.findByText('Writing changes 2 songs: 1 .lrc, 1 .txt, 1 in tags.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Write lyrics to 2 songs' }))
  expect(await screen.findByText(/Writing lyrics to 2 songs\. Undo it from the edit history/)).toBeInTheDocument()
  expect(await posted('/api/servers/1/files/lyrics/5/apply')).toEqual([{ placement: 'both', overwrite: false }])
})

it("can't write while writing to files is off", async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <LyricsDialog serverId={1} albums={[]} canWrite={false} onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(screen.getByText(/for the whole library/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Look up lyrics' }))
  expect(await screen.findByRole('button', { name: 'Write lyrics to 2 songs' })).toBeDisabled()
  expect(screen.getByText(/Writing to the files is off/)).toBeInTheDocument()
})
