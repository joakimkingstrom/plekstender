import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LyricsDialog } from './LyricsDialog'

const job = { id: 5, status: 'succeeded', progress: { done: 3, total: 3 }, stats: {}, error: null, created_at: '2026-11-24T10:00:00Z', finished_at: '2026-11-24T10:01:00Z' }

let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>
let asFound: { running: boolean } | null = null

beforeEach(() => {
  asFound = null
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    const url = new URL(input.url)
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    if (url.pathname === '/api/servers/1/files/lyrics' && input.method === 'POST') return json({ ...job, status: 'queued' }, 201)
    if (url.pathname === '/api/servers/1/files/lyrics/5') {
      const tags = url.searchParams.get('embedded') === 'true'
      if (asFound)
        return json({
          job: { ...job, status: asFound.running ? 'running' : 'succeeded', progress: { done: 40, total: 100, looked_up: 30, written: 25 } },
          files: 100, looked_up: 40, synced: 20, word_synced: 5, plain: 5, instrumental: 0, none: 15, untagged: 0, errors: 0, had_lyrics: 0,
          lrc: 0, txt: 0, lyricsfile: 0, lyrics: 0, songs: 0, sample: [], applied_set_id: 9,
        })
      return json({
        job,
        files: 3, looked_up: 3, synced: 1, word_synced: 1, plain: 1, instrumental: 0, none: 1, untagged: 0, errors: 0, had_lyrics: 1,
        lrc: 1, txt: 1, lyricsfile: 0, lyrics: tags ? 1 : 0, songs: 2,
        sample: [
          { path: 'Radiohead/OK Computer/02 Paranoid Android.flac', status: 'synced', lrc: true, txt: false, lyricsfile: false, word_synced: true, lyrics: tags, had_lyrics: false, preview: 'Please could you stop the noise\nI am trying to get some rest' },
          { path: 'Radiohead/OK Computer/06 Karma Police.flac', status: 'plain', lrc: false, txt: true, lyricsfile: false, word_synced: false, lyrics: false, had_lyrics: true, preview: 'Karma police' },
        ],
        applied_set_id: null,
      })
    }
    if (url.pathname === '/api/servers/1/files/lyrics/5/stop')
      return json({ ...job, status: 'running' })
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
  // Looked up first, written afterwards.
  fireEvent.click(screen.getByRole('checkbox', { name: /Write lyrics as they're found/ }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'Look again for songs looked up before' }))
  fireEvent.click(screen.getByRole('button', { name: 'Look up lyrics' }))
  expect(await screen.findByText('Writing changes 2 songs: 1 .lrc, 1 .txt.')).toBeInTheDocument()
  expect(await posted('/api/servers/1/files/lyrics')).toEqual([{ albums: [200, 201], again: true, write: null }])
  expect(screen.getByText(/Of 3 songs; 1 already had lyrics/)).toBeInTheDocument()
  expect(screen.getByText('.txt · replaces lyrics it had · Karma police')).toBeInTheDocument()
  expect(screen.getByText('.lrc · word-synced · Please could you stop the noise')).toBeInTheDocument()
  expect(screen.getByText('Word-synced').nextElementSibling).toHaveTextContent('1')

  fireEvent.click(screen.getByRole('checkbox', { name: /In each song's tags/ }))
  expect(await screen.findByText('Writing changes 2 songs: 1 .lrc, 1 .txt, 1 in tags.')).toBeInTheDocument()
  // Nowhere to write: nothing to do.
  fireEvent.click(screen.getByRole('checkbox', { name: /Next to each song as \.lrc or \.txt/ }))
  fireEvent.click(screen.getByRole('checkbox', { name: /In each song's tags/ }))
  expect(await screen.findByText('Choose where to write them.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: /Next to each song as \.lrc or \.txt/ }))
  fireEvent.click(screen.getByRole('checkbox', { name: /In each song's tags/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Write lyrics to 2 songs' }))
  expect(await screen.findByText(/Writing lyrics to 2 songs\. Undo it from the edit history/)).toBeInTheDocument()
  expect(await posted('/api/servers/1/files/lyrics/5/apply')).toEqual([
    { sidecar: true, lyricsfile: false, embedded: true, overwrite: false },
  ])
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

it('writes lyrics as they are found, as one edit', async () => {
  asFound = { running: true }
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <LyricsDialog serverId={1} albums={[]} canWrite onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(screen.getByRole('checkbox', { name: /Write lyrics as they're found/ })).toBeChecked()
  fireEvent.click(screen.getByRole('checkbox', { name: /Next to each song as a Lyricsfile/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Look up lyrics' }))
  expect(await screen.findByText(/Looking up and writing lyrics… 40 of 100 songs · 25 files written/)).toBeInTheDocument()
  expect(await posted('/api/servers/1/files/lyrics')).toEqual([
    { albums: [], again: false, write: { sidecar: true, lyricsfile: true, embedded: false, overwrite: false } },
  ])
  expect(screen.getByText(/Lyrics are written as they're found, as one edit/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Write lyrics to/ })).not.toBeInTheDocument()

  // Cancelling asks first, and can leave what it wrote in place.
  fireEvent.click(screen.getByRole('button', { name: 'Cancel…' }))
  const confirm = await screen.findByRole('dialog', { name: 'Cancel the lookup?' })
  expect(within(confirm).getByText('25 files so far, undone as one edit.')).toBeInTheDocument()
  fireEvent.click(within(confirm).getByRole('checkbox', { name: /Take out the lyrics it wrote too/ }))
  fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel the lookup' }))
  await vi.waitFor(async () => expect(await posted('/api/servers/1/files/lyrics/5/stop')).toEqual([{ cancel: true, undo: false }]))
  expect(await screen.findByText('Cancelling…')).toBeInTheDocument()
})

it('stops a lookup, keeping what it found', async () => {
  asFound = { running: true }
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <LyricsDialog serverId={1} albums={[]} canWrite onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'Look up lyrics' }))
  const stopButton = await screen.findByRole('button', { name: 'Stop' })
  asFound = { running: false } // what the lookup says once it has stopped
  fireEvent.click(stopButton)
  await vi.waitFor(async () => expect(await posted('/api/servers/1/files/lyrics/5/stop')).toEqual([{ cancel: false, undo: true }]))
  // Stopped: the lookup and where to write are offered again; no write-afterwards section
  // while "as they're found" is ticked.
  expect(await screen.findByRole('button', { name: 'Look up again' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Write lyrics to/ })).not.toBeInTheDocument()
})
