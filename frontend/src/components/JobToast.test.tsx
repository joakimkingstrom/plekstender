import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { JobToast } from './JobToast'

const job = (status: string, progress: object, stats: object = {}) => ({
  id: 7, kind: 'covers', status, progress, stats, error: null, created_at: '2026-11-21T12:00:00Z', finished_at: null, download: null,
})  // prettier-ignore
let jobs: unknown[] = []
/** Wait until the jobs have been asked for (and the answer handled). */
const jobsLoaded = async () => {
  await vi.waitFor(() =>
    expect(
      vi.mocked(fetch).mock.calls.some(([input]) => String(typeof input === 'string' ? input : (input as Request).url).includes('/artwork/jobs')),
    ).toBe(true),
  )
  await new Promise((r) => setTimeout(r, 20))
}

beforeEach(() => {
  jobs = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/servers')
        return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      if (url.pathname === '/api/servers/1/artwork/jobs') return json(jobs)
      return new Response('{}', { status: 404 })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const renderAt = (path: string, qc = new QueryClient()) => {
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <JobToast />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return qc
}

describe('JobToast', () => {
  it('shows a running job’s progress on any page, then what it did', async () => {
    jobs = [job('running', { done: 3, total: 10 })]
    const qc = renderAt('/stats')
    expect(await screen.findByText('Applying best album covers…')).toBeInTheDocument()
    expect(screen.getByText('3 / 10')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Applying best album covers' })).toHaveAttribute('aria-valuenow', '3')
    expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/library/artwork/albums')

    jobs = [job('succeeded', { done: 10, total: 10 }, { applied: 7, not_found: 3, already: 0, failed: 0 })]
    await qc.invalidateQueries()
    expect(await screen.findByText('✓ Best album covers applied')).toBeInTheDocument()
    expect(screen.getByText('7 applied, 3 without a match')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('isn’t shown on the job’s own page, or for jobs that finished before', async () => {
    jobs = [job('running', { done: 3, total: 10 })]
    renderAt('/library/artwork/albums')
    await jobsLoaded()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    cleanup()

    vi.mocked(fetch).mockClear()
    jobs = [job('succeeded', { done: 10, total: 10 }, { applied: 7 })]
    renderAt('/stats')
    await jobsLoaded()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
