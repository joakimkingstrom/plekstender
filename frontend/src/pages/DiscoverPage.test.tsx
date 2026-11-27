import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DiscoverPage } from './DiscoverPage'

const muse = {
  key: 'muse', name: 'Muse', similar_to: 12, listens: 42,
  sources: [{ name: 'Radiohead', rating_key: 100 }, { name: 'Placebo', rating_key: 105 }],
}
const massive = { key: 'massive attack', name: 'Massive Attack', similar_to: 3, listens: 0, sources: [{ name: 'Portishead', rating_key: 101 }] }
let requests: Request[] = []

beforeEach(() => {
  requests = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      requests.push(input)
      const url = new URL(input.url)
      const json = (body: unknown, status = 200) =>
        new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
      switch (url.pathname) {
        case '/api/servers':
          return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
        case '/api/servers/1/sync':
          return json({ active: null, last: null, last_sync_at: null, counts: { artists: 3, albums: 3, tracks: 30, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
        case '/api/servers/1/library/discover':
          return url.searchParams.get('hidden') === 'true'
            ? json({ items: [], total: 0, hidden: 1 })
            : json({ items: [muse, massive], total: 2, hidden: 0 })
        case '/api/servers/1/library/discover/hidden':
          return json(null, 204)
        default:
          return json({ detail: 'Not found' }, 404)
      }
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Discover', () => {
  it('lists artists to add, why, and hides one', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <DiscoverPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const first = (await screen.findByText('Muse')).closest('li')!
    expect(first).toHaveTextContent('similar to 12 of your artists')
    expect(first).toHaveTextContent('42 plays')
    expect(within(first).getByRole('meter', { name: 'Your plays of Muse' })).toHaveAttribute('aria-valuenow', '42')
    expect(first).toHaveTextContent('Radiohead, Placebo and 10 more')
    expect(within(first).getByRole('link', { name: 'Radiohead' })).toHaveAttribute('href', '/library/artists/100')
    expect(within(first).getByRole('link', { name: 'Muse on Last.fm' })).toHaveAttribute('href', 'https://www.last.fm/music/Muse')
    expect(screen.getByText('Massive Attack').closest('li')).toHaveTextContent('similar to 3 of your artistsPortishead')

    fireEvent.click(within(first).getByRole('button', { name: 'Hide Muse' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'POST')).toBe(true))
    const posted = requests.find((r) => r.method === 'POST')!
    expect(await posted.clone().json()).toEqual({ key: 'muse', name: 'Muse' })
  })
})
