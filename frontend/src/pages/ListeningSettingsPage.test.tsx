import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { ConnectionsPage } from './ConnectionsPage'
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

function renderPage(responses: Record<string, unknown>, page: ReactNode = <ListeningSettingsPage />) {
  responses = {
    'GET /api/listens/plex': [],
    'GET /api/listens/imports/listenbrainz': { available: true, username: null, active: null, last: null },
    'GET /api/listens/listenbrainz': { connected: false, username: null, enabled_at: null, sent: 0, pending: 0, last_submitted_at: null, paused: false, error: null },
    ...responses,
  }
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
        {page}
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
    expect(within(input.closest('section')!).getByRole('button', { name: 'Import' })).toBeDisabled() // too short
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

describe('Scrobble to ListenBrainz', () => {
  it('asks for a token, then shows what was sent', async () => {
    const connected = {
      connected: true,
      username: 'joakim_lb',
      enabled_at: '2026-11-18T12:00:00Z',
      sent: 12,
      pending: 2,
      last_submitted_at: '2026-11-18T12:30:00Z',
      paused: false,
      error: null,
    }
    const fetch = renderPage({
      'GET /api/auth/me': user,
      'GET /api/listens/lastfm': { available: false, username: null, active: null, last: null },
      'PUT /api/listens/listenbrainz': connected,
    }, <ConnectionsPage />)
    const input = await screen.findByLabelText('User token')
    fireEvent.change(input, { target: { value: 'abcd-1234-efgh' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByText('joakim_lb')).toBeInTheDocument()
    expect(screen.getByText(/12 plays sent/)).toBeInTheDocument()
    expect(screen.getByText(/2 waiting/)).toBeInTheDocument()
    const put = fetch.mock.calls.find(([r]) => r.method === 'PUT')
    expect(await put![0].json()).toEqual({ token: 'abcd-1234-efgh' })
  })

  it('asks for a new token when ListenBrainz rejected the old one', async () => {
    renderPage({
      'GET /api/auth/me': user,
      'GET /api/listens/lastfm': { available: false, username: null, active: null, last: null },
      'GET /api/listens/listenbrainz': {
        connected: true,
        username: 'joakim_lb',
        enabled_at: '2026-11-18T12:00:00Z',
        sent: 3,
        pending: 1,
        last_submitted_at: null,
        paused: true,
        error: 'ListenBrainz rejected the token, so scrobbling is paused. Paste a new token to resume.',
      },
    }, <ConnectionsPage />)
    expect(await screen.findByText(/scrobbling is paused/)).toBeInTheDocument()
    expect(screen.getByLabelText('New user token')).toBeInTheDocument()
  })
})

describe('Send history to ListenBrainz', () => {
  it('shows the last run and sends again', async () => {
    const lb = { connected: true, username: 'joakim_lb', enabled_at: '2026-11-18T12:00:00Z', sent: 3, pending: 0, last_submitted_at: null, paused: false, error: null }
    const sendJob = { ...finished, id: 11, source: 'lb_send', username: 'joakim_lb', progress: { total: 900, done: 900 }, stats: { sent: 850, existing: 50, refused: 0 } }
    const fetch = renderPage(
      {
        'GET /api/auth/me': user,
        'GET /api/listens/listenbrainz': lb,
        'GET /api/listens/listenbrainz/send': { available: true, username: 'joakim_lb', active: null, last: sendJob },
        'POST /api/listens/listenbrainz/send': { ...sendJob, id: 12, status: 'queued', stats: {} },
      },
      <ConnectionsPage />,
    )
    expect(await screen.findByText(/850 sent, 50 already there/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Send again' }))
    await vi.waitFor(() =>
      expect(fetch.mock.calls.some(([r]) => r.method === 'POST' && r.url.endsWith('/api/listens/listenbrainz/send'))).toBe(true),
    )
  })
})

describe('Import from ListenBrainz', () => {
  it('starts an import for a username', async () => {
    const fetch = renderPage({
      'GET /api/auth/me': user,
      'GET /api/listens/lastfm': { available: false, username: null, active: null, last: null },
      'POST /api/listens/imports/listenbrainz': { ...finished, id: 9, source: 'listenbrainz', status: 'queued' },
    })
    const input = await screen.findByLabelText('ListenBrainz username')
    fireEvent.change(input, { target: { value: 'joakim_lb' } })
    const card = input.closest('section')!
    fireEvent.click(within(card).getByRole('button', { name: 'Import' }))
    await vi.waitFor(() =>
      expect(
        fetch.mock.calls.some(([r]) => r.method === 'POST' && r.url.endsWith('/api/listens/imports/listenbrainz')),
      ).toBe(true),
    )
  })
})
