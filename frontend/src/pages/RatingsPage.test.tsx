import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RatingsPage } from './RatingsPage'

const now = new Date()
const yesterday = new Date(now.getTime() - 86_400_000)
const item = (key: number, kind: string, title: string, subtitle: string | null, rating: number, at: Date | null) => ({
  rating_key: key,
  kind,
  title,
  subtitle,
  thumb: null,
  user_rating: rating,
  rated_at: at?.toISOString() ?? null,
  link_key: kind === 'track' ? 200 : key,
})

let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>

beforeEach(() => {
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    const url = new URL(input.url)
    const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
    if (url.pathname === '/api/servers')
      return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
    if (url.pathname === '/api/servers/1/sync')
      return json({ active: null, last: null, last_sync_at: null, counts: { artists: 1, albums: 1, tracks: 2, rated_tracks: 1, plays: 0 }, incremental_minutes: 30, full_days: 7 })
    if (url.pathname === '/api/servers/1/ratings' && input.method === 'GET') {
      const all = [
        item(303, 'track', 'Lucky', 'Radiohead · OK Computer', 6, now),
        item(202, 'album', 'Dummy', 'Portishead', 9, now),
        item(100, 'artist', 'Radiohead', null, 8, yesterday),
        item(300, 'track', 'Paranoid Android', 'Radiohead · OK Computer', 10, null),
      ]
      const kind = url.searchParams.get('kind')
      const items = kind ? all.filter((i) => i.kind === kind) : all
      return json({ items, total: items.length })
    }
    if (url.pathname === '/api/servers/1/ratings' && input.method === 'POST') {
      const body = await input.clone().json()
      return json({ rating_key: body.rating_key, item_type: 'track', user_rating: body.rating })
    }
    return json({ detail: 'Not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('RatingsPage', () => {
  it('lists ratings newest first, by day, and filters by kind', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <RatingsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const today = await screen.findByRole('region', { name: 'Today' })
    expect(within(today).getAllByRole('link').map((a) => a.textContent)).toEqual(['Lucky', 'Dummy'])
    expect(within(screen.getByRole('region', { name: 'Yesterday' })).getByText('Radiohead')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Earlier (no date)' })).getByText('Paranoid Android')).toBeInTheDocument()
    expect(screen.getByText('4 rated items, most recent first')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Songs' }))
    expect(await screen.findByText('2 rated songs, most recent first')).toBeInTheDocument()
    expect(screen.queryByText('Dummy')).not.toBeInTheDocument()

    // Changing a rating saves it to Plex.
    fireEvent.keyDown(screen.getByRole('slider', { name: 'Rate Lucky' }), { key: '4' })
    await vi.waitFor(async () => {
      const post = fetchMock.mock.calls.find(([r]) => r.method === 'POST')
      expect(await post![0].clone().json()).toEqual({ rating_key: 303, rating: 8 })
    })
  })
})
