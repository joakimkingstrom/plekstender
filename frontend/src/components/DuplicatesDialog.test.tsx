import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { DuplicatesDialog } from './DuplicatesDialog'

const file = (file_id: number, path: string, extra: object = {}) => ({
  file_id,
  path,
  format: 'flac',
  size: 30_000_000,
  duration_ms: 383_000,
  kbps: 627,
  rating_key: 300,
  title: 'Paranoid Android',
  artist: 'Radiohead',
  album: 'OK Computer',
  sidecars: [],
  keep: false,
  ...extra,
})
const found = (finishedAt: string) => ({
  job: { id: 4, status: 'succeeded', progress: {}, stats: {}, error: null, created_at: finishedAt, finished_at: finishedAt },
  groups: [
    {
      confirmed: true,
      similarity: 0.97,
      files: [
        file(1, 'Radiohead/OK Computer/02 Paranoid Android.flac', { keep: true }),
        file(2, 'Downloads/Paranoid Android.mp3', { format: 'mp3', size: 9_000_000, kbps: 188, rating_key: null, sidecars: ['Downloads/Paranoid Android.lrc'] }),
      ],
    },
    { confirmed: false, similarity: null, files: [file(3, 'a/Intro.flac', { title: 'Intro' }), file(5, 'b/Intro.flac', { title: 'Intro' })] },
  ],
  total_groups: 2,
  files: 1200,
  fingerprint_error: null,
  applied_set_id: null,
})

let latest: unknown
let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>

beforeEach(() => {
  latest = found(new Date().toISOString())
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    const url = new URL(input.url)
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    if (url.pathname === '/api/servers/1/files/duplicates/latest') return json(latest)
    if (url.pathname === '/api/servers/1/files/duplicates' && input.method === 'POST') {
      latest = { ...found(new Date().toISOString()), job: { ...found('').job, status: 'running', progress: { done: 10, total: 40 } } }
      return json({ id: 5, status: 'queued', progress: {}, stats: {}, error: null, created_at: '2026-11-24T10:00:00Z', finished_at: null }, 201)
    }
    if (url.pathname === '/api/servers/1/files/duplicates/4/apply')
      return json({ id: 9, kind: 'edit', description: 'Moved', status: 'queued', error: null, user: 'me', created_at: '2026-11-24T10:02:00Z', applied_at: null, total: 2, counts: {}, undoable: 0, changes: [] }, 201)
    return json({ detail: 'Not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderDialog(canWrite = true) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <DuplicatesDialog serverId={1} canWrite={canWrite} onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

it('suggests the copy to keep, leaves unchecked groups alone, and moves the rest', async () => {
  renderDialog()
  expect(await screen.findByText('2 songs are here more than once.')).toBeInTheDocument()
  expect(screen.getByText('Same recording · 97% alike')).toBeInTheDocument()
  expect(screen.getByText('Same name and length; not compared by sound')).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: /Keep Radiohead\/OK Computer\/02 Paranoid Android\.flac/ })).toBeChecked()
  expect(screen.getByText(/MP3 · 188 kbps · 9\.0 MB .* not in Plex · with a lyrics file/)).toBeInTheDocument()
  const [, leaveAlone] = screen.getAllByRole('radio', { name: 'Keep them all' })
  expect(leaveAlone).toBeChecked()
  const move = screen.getByRole('button', { name: 'Move 1 copy out of the library (9.0 MB)' })
  // Picking a copy in the unchecked group adds the other one.
  fireEvent.click(screen.getByRole('radio', { name: /Keep b\/Intro\.flac/ }))
  expect(screen.getByRole('button', { name: 'Move 2 copies out of the library (39.0 MB)' })).toBe(move)
  fireEvent.click(move)
  expect(await screen.findByText(/Moving 2 files into \.plekstender-duplicates/)).toBeInTheDocument()
  const post = fetchMock.mock.calls.find(([r]) => r.url.endsWith('/duplicates/4/apply'))!
  expect(await post[0].clone().json()).toEqual({ remove: [2, 3] })
})

it('offers a new check when the last one is old, and shows its progress', async () => {
  latest = found('2026-01-01T00:00:00Z')
  renderDialog()
  expect(await screen.findByText('The last check is more than a day old.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
  expect(await screen.findByText(/Comparing files by sound… 10 of 40/)).toBeInTheDocument()
})
