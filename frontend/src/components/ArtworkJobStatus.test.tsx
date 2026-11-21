import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationBell } from './NotificationBell'

const job = (status: string, progress: object, stats: object = {}) => ({
  id: 7, kind: 'covers', status, progress, stats, error: null, created_at: '2026-11-21T12:00:00Z', finished_at: null, download: null,
})  // prettier-ignore
let jobs: unknown[] = []
const fetchMock = vi.fn(async (input: Request | string) => {
  const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
  const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
  if (url.pathname === '/api/servers')
    return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
  if (url.pathname === '/api/servers/1/artwork/jobs') return json(jobs)
  if (url.pathname === '/api/notifications') return json({ items: [], unread: 0 })
  return new Response(null, { status: 204 })
})
/** Wait until the jobs have been asked for (and the answer handled). */
const jobsLoaded = async () => {
  await vi.waitFor(() => expect(fetchMock.mock.calls.some(([i]) => String(typeof i === 'string' ? i : i.url).includes('/artwork/jobs'))).toBe(true))
  await new Promise((r) => setTimeout(r, 20))
}

beforeEach(() => {
  jobs = []
  fetchMock.mockClear()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const renderBell = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <NotificationBell isAdmin />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return qc
}

describe('An artwork job in the bell', () => {
  it('shows how far a running job has got, then what it did', async () => {
    jobs = [job('running', { done: 3, total: 10 })]
    const qc = renderBell()
    const bell = await screen.findByRole('button', { name: 'Notifications (an artwork job is running)' })
    fireEvent.click(bell)
    expect(screen.getByText('Applying best album covers…')).toBeInTheDocument()
    expect(screen.getByText('3 / 10')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Applying best album covers' })).toHaveAttribute('aria-valuenow', '3')
    expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/library/artwork/albums')
    fireEvent.click(bell) // closed

    // Done: one new in the bell until it's opened.
    jobs = [job('succeeded', { done: 10, total: 10 }, { applied: 7, not_found: 3, already: 0, failed: 0 })]
    await qc.invalidateQueries()
    fireEvent.click(await screen.findByRole('button', { name: 'Notifications: 1 new' }))
    expect(screen.getByText('Best album covers applied')).toBeInTheDocument()
    expect(screen.getByText('7 applied, 3 without a match')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument() // read
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss: Best album covers applied' }))
    expect(screen.queryByText('Best album covers applied')).not.toBeInTheDocument()
    expect(screen.getByText('Nothing needs a look. Everything\'s working.')).toBeInTheDocument()
  })

  it('leaves out jobs that finished before this visit', async () => {
    jobs = [job('succeeded', { done: 10, total: 10 }, { applied: 7 })]
    renderBell()
    await jobsLoaded()
    fireEvent.click(screen.getByRole('button', { name: 'Notifications' }))
    expect(screen.queryByText('Best album covers applied')).not.toBeInTheDocument()
  })
})
