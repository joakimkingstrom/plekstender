import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ListeningSettingsPage } from './ListeningSettingsPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const user = { id: 2, username: 'friend', email: null, thumb_url: null, is_admin: false }
const finished = {
  id: 7,
  source: 'lastfm',
  username: 'Joakim',
  status: 'succeeded',
  window_from: null,
  window_to: '2026-11-18T12:00:00Z',
  created_at: '2026-11-18T12:00:00Z',
  started_at: '2026-11-18T12:00:01Z',
  finished_at: '2026-11-18T12:03:00Z',
  progress: { page: 3, pages: 3, done: 120, total: 120 },
  stats: { added: 118, existing: 2 },
  error: null,
}

function renderPage(responses: Record<string, unknown>) {
  responses = { 'GET /api/listens/plex': [], ...responses }
  const fetch = vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname
    const body = responses[`${input.method} ${path}`]
    return new Response(JSON.stringify(body ?? { detail: 'Not found' }), {
      status: body === undefined ? 404 : 200,
      headers: { 'content-type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <ListeningSettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetch
}

describe('ListeningSettingsPage', () => {
  it('tells non-admins to ask for an API key when Last.fm import is not set up', async () => {
    renderPage({
      'GET /api/auth/me': user,
      'GET /api/listens/lastfm': { available: false, username: null, active: null, last: null },
    })
    expect(await screen.findByText(/Ask an admin to add a Last.fm API key/)).toBeInTheDocument()
    expect(screen.queryByText('Last.fm API key')).not.toBeInTheDocument() // admin-only card
  })

  it('shows the last import and offers to fetch new scrobbles', async () => {
    const fetch = renderPage({
      'GET /api/auth/me': user,
      'GET /api/listens/lastfm': { available: true, username: 'Joakim', active: null, last: finished },
      'POST /api/listens/lastfm': { ...finished, id: 8, status: 'queued' },
    })
    expect(await screen.findByText(/118 new listens/)).toBeInTheDocument()
    expect(screen.getByText(/2 were already here/)).toBeInTheDocument()
    const input = screen.getByLabelText('Last.fm username')
    expect(input).toHaveValue('Joakim')

    const button = screen.getByRole('button', { name: 'Import new scrobbles' })
    fireEvent.click(button)
    await vi.waitFor(() =>
      expect(fetch.mock.calls.some(([r]) => r.method === 'POST' && r.url.endsWith('/api/listens/lastfm'))).toBe(true),
    )

    fireEvent.change(input, { target: { value: 'x' } })
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled() // too short
  })

  it('shows progress while an import runs', async () => {
    renderPage({
      'GET /api/auth/me': user,
      'GET /api/listens/lastfm': {
        available: true,
        username: 'Joakim',
        active: { ...finished, status: 'running', finished_at: null, progress: { done: 400, total: 1000 } },
        last: null,
      },
    })
    expect(await screen.findByText("Importing Joakim's scrobbles…")).toBeInTheDocument()
    expect(screen.getByText('400 / 1,000')).toBeInTheDocument()
    expect(screen.queryByLabelText('Last.fm username')).not.toBeInTheDocument()
  })
})

describe('Plays from Plex', () => {
  it('shows each server with its play count and state', async () => {
    renderPage({
      'GET /api/auth/me': user,
      'GET /api/listens/lastfm': { available: false, username: null, active: null, last: null },
      'GET /api/listens/plex': [
        { server_id: 1, server_name: 'Home', via_owner: true, checked_at: '2026-11-18T12:00:00Z', backfilled: false, error: null, listens: 41746 },
        { server_id: 2, server_name: 'Cabin', via_owner: false, checked_at: '2026-11-18T12:00:00Z', backfilled: true, error: "Plex didn't allow reading play history with this account.", listens: 0 },
      ],
    })
    expect(await screen.findByText('41,746 plays')).toBeInTheDocument()
    expect(screen.getByText('Reading past plays…')).toBeInTheDocument()
    expect(screen.getByText(/didn't allow reading play history/)).toBeInTheDocument()
  })
})
