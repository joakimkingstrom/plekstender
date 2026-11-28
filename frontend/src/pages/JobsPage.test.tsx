import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { JobsPage } from './JobsPage'

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString()
const job = (extra: object) => ({
  kind: 'files', id: 1, what: 'Finding duplicate files', subkind: 'dupes', status: 'running', server_id: 1,
  created_at: ago(130), started_at: ago(125), finished_at: null, progress: {}, error: null,
  stop_requested: null, stoppable: true, waiting_for: null, ...extra,
})

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          active: [
            job({ progress: { done: 1200, total: 99000 } }),
            job({ id: 3, what: "Reading the music files' tags", subkind: 'scan', progress: { step: 'Folder pictures', done: 200, total: 15000 } }),
            job({ id: 2, what: "Reading the music files' tags", subkind: 'scan', status: 'queued', started_at: null, created_at: ago(20), waiting_for: { what: 'Finding duplicate files', since: ago(125) } }),
          ],
          finished: [
            job({ kind: 'edits', id: 9, what: 'Identified by sound: 119 files', subkind: 'edit', status: 'applied', finished_at: ago(5), started_at: ago(6) }),
            job({ kind: 'edits', id: 8, what: 'Identified by sound: 2 files', subkind: 'edit', status: 'failed', finished_at: ago(7), error: "'UFID' object is not iterable" }),
          ],
        }),
        { headers: { 'content-type': 'application/json' } },
      ),
    ),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('lists running and waiting jobs (and what a waiting one waits for), then finished ones', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <JobsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const active = (await screen.findByRole('heading', { name: 'Running and waiting' })).closest('section')!
  expect(within(active).getByText('1,200 / 99,000')).toBeInTheDocument()
  // A job with steps (a scan, after the tags) says which it's on.
  expect(within(active).getByText('Folder pictures · 200 / 15,000')).toBeInTheDocument()
  expect(within(active).getByRole('button', { name: 'Stop finding duplicate files' })).toBeInTheDocument()
  expect(within(active).getByText(/Waiting for:/).textContent).toMatch(/Finding duplicate files \(started 2 hours ago\)/)
  expect(within(active).getAllByRole('link', { name: 'Folder scan →' })[0]).toHaveAttribute('href', '/settings/files/scan')
  const finished = screen.getByRole('heading', { name: 'Finished' }).closest('section')!
  expect(within(finished).getByText('Written')).toBeInTheDocument()
  expect(within(finished).getByText('Failed')).toBeInTheDocument()
  expect(within(finished).getByText("'UFID' object is not iterable")).toBeInTheDocument()
})
