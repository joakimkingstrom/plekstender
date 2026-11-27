import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { GigPlaylistsCard } from './GigPlaylistsCard'

let preferences: Record<string, unknown>
let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>

beforeEach(() => {
  preferences = { gig_playlists: { before: false, after: true } }
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    const path = new URL(input.url).pathname
    const json = (body: unknown, status = 200) =>
      new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    const me = () => ({ id: 1, username: 'me', email: null, thumb_url: null, is_admin: false, preferences })
    if (path === '/api/auth/me/preferences') {
      preferences = await input.clone().json()
      return json(me())
    }
    if (path === '/api/auth/me') return json(me())
    if (path === '/api/gigs/playlists/check') return json(null, 204)
    if (path === '/api/gigs/playlists')
      return json({
        setlistfm: true,
        items: [
          { kind: 'before', gig_date: '2026-12-05', songs: 14, playlist_id: 7, name: 'Before the gig: Radiohead, 5 Dec 2026', server_id: 1, status: 'ok' },
          { kind: 'after', gig_date: '2026-11-20', songs: 0, playlist_id: null, name: null, server_id: null, status: null },
        ],
      })
    return json({ detail: 'Not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('turns gig playlists on and off, and lists the ones made', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <GigPlaylistsCard />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const before = await screen.findByRole('checkbox', { name: /Before a gig/ })
  expect(await screen.findByRole('checkbox', { name: /After a gig/ })).toBeChecked()
  expect(before).not.toBeChecked()
  expect(await screen.findByRole('link', { name: 'Before the gig: Radiohead, 5 Dec 2026' })).toHaveAttribute('href', '/library/playlists/7')
  expect(screen.getByText(/none of its songs are in your library/)).toBeInTheDocument()

  // Turning one on saves it, and asks for the playlists now rather than within the hour.
  fireEvent.click(before)
  await vi.waitFor(() => expect(preferences).toMatchObject({ gig_playlists: { before: true, after: true } }))
  await vi.waitFor(() =>
    expect(fetchMock.mock.calls.some(([r]) => new URL(r.url).pathname === '/api/gigs/playlists/check')).toBe(true),
  )
  expect(await screen.findByText(/playlists show up in a minute or two/)).toBeInTheDocument()
})

it('deletes them some time after the gig, if asked', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <GigPlaylistsCard />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  await vi.waitFor(() => expect(screen.getByRole('checkbox', { name: /After a gig/ })).toBeChecked())
  const remove = screen.getByRole('checkbox', { name: 'Delete them afterwards' })
  expect(remove).not.toBeChecked()
  expect(screen.getByText('Kept until you delete them.')).toBeInTheDocument()

  // On: two weeks after the gig, to start with; the other choices are kept.
  fireEvent.click(remove)
  await vi.waitFor(() =>
    expect(preferences).toMatchObject({ gig_playlists: { after: true, delete_after: 2, delete_unit: 'weeks' } }),
  )
  fireEvent.click(await screen.findByRole('button', { name: /Days, weeks or months/ }))
  fireEvent.click(await screen.findByRole('option', { name: 'months' }))
  await vi.waitFor(() => expect(preferences).toMatchObject({ gig_playlists: { delete_after: 2, delete_unit: 'months' } }))

  // Off again: kept.
  fireEvent.click(screen.getByRole('checkbox', { name: 'Delete them afterwards' }))
  await vi.waitFor(() => expect(preferences).toMatchObject({ gig_playlists: { delete_after: 0 } }))
})
