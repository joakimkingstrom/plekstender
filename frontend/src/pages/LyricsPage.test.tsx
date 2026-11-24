import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LyricsPage } from './LyricsPage'

let latest: unknown = null

beforeEach(() => {
  latest = null
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname
      const body: Record<string, unknown> = {
        '/api/servers': [{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }],
        '/api/servers/1/sync': { active: null, last: null, last_sync_at: null, counts: { artists: 2, albums: 1, tracks: 4, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 },
        '/api/servers/1/files/summary': { mapped: true, tag_editing: true, files: 1000, matched: 990, errors: 0, active: null, last: null },
        '/api/servers/1/files/lyrics/coverage': { files: 1000, word: 50, synced: 300, plain: 150, instrumental: 20, none: 400, unchecked: 80 },
        '/api/servers/1/files/lyrics/latest': latest,
      }
      return new Response(JSON.stringify(body[path] ?? { detail: 'Not found' }), {
        status: path in body ? 200 : 404,
        headers: { 'content-type': 'application/json' },
      })
    }),
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('shows how much of the library has which kind of lyrics, and offers to get more', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <LyricsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const card = (await screen.findByRole('heading', { name: 'Lyrics in your library' })).closest('section')!
  // 500 of the 920 songs looked at have lyrics.
  expect(await within(card).findByText('54%')).toBeInTheDocument()
  const table = within(card).getByRole('table', { name: 'Songs by kind of lyrics' })
  expect(within(table).getAllByRole('row').map((r) => r.textContent)).toEqual([
    'Word-synced50 songs5.4%',
    'Synced300 songs33%',
    'Plain150 songs16%',
    'Instrumental20 songs2.2%',
    'No lyrics400 songs43%',
  ])
  expect(within(card).getByText(/80 songs haven't been looked at/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Look up lyrics' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Tag editing' })).toHaveAttribute('href', '/library/tag-editing')
})
