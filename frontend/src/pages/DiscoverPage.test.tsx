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
        case '/api/lidarr':
          return json({ available: true })
        case '/api/lidarr/artists/lookup':
          return json([
            { mbid: '9c9f1380-2516-4fc9-a3e6-f9f61941d090', name: 'Muse', disambiguation: 'UK rock band', kind: 'Group', overview: 'English rock band.', in_lidarr: false },
            { mbid: '11112222-3333-4444-8555-666677778888', name: 'Muse', disambiguation: 'Japanese idol', kind: null, overview: null, in_lidarr: false },
          ])
        case '/api/lidarr/artists':
          return json({ id: 1, name: 'Muse' }, 201)
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
  it('shows the top 3 as cards with their artwork, 4–10 with their banner, then a list', async () => {
    const many = Array.from({ length: 12 }, (_, i) => ({
      key: `artist ${i + 1}`,
      name: `Artist ${i + 1}`,
      similar_to: 20 - i,
      listens: 0,
      sources: [{ name: 'Radiohead', rating_key: 100 }],
      art: i < 10 ? { background: i < 3 ? `/bg${i + 1}` : null, banner: i === 6 ? null : `/banner${i + 1}`, logo: i === 0 ? '/logo1' : null } : null,
    }))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: Request) => {
        const path = new URL(input.url).pathname
        const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
        if (path === '/api/servers') return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
        if (path === '/api/servers/1/sync') return json({ active: null, last: null, last_sync_at: null, counts: { artists: 3, albums: 3, tracks: 30, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
        if (path === '/api/servers/1/library/discover') return json({ items: many, total: 12, hidden: 0 })
        return json({ available: false })
      }),
    )
    const { container } = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <DiscoverPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const top = await screen.findByRole('list', { name: 'Top suggestions' })
    expect(within(top).getAllByRole('listitem')).toHaveLength(3)
    // The logo stands for the name (an image named after the artist); no logo: the name.
    expect(within(top).getByRole('img', { name: 'Artist 1' })).toHaveAttribute('src', '/logo1')
    expect(within(top).getByRole('heading', { name: 'Artist 2' })).toBeInTheDocument()
    expect(container.querySelector('img[src="/bg2"]')).not.toBeNull()
    // 4–10: banners (7 has none: its name instead); 11 on: plain rows.
    expect(container.querySelector('img[src="/banner4"]')).not.toBeNull()
    expect(container.querySelector('img[src="/banner7"]')).toBeNull()
    const row11 = screen.getByText('Artist 11').closest('li')!
    expect(row11.querySelector('img')).toBeNull()
    expect(row11).toHaveTextContent('11')
  })

  it('lists artists to add, why, and hides one', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <DiscoverPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const first = (await screen.findByText('Muse')).closest('li')!
    expect(first).toHaveTextContent(/similar to 12 of your artists/i)
    expect(first).toHaveTextContent('42 plays')
    expect(within(first).getByRole('meter', { name: 'Your plays of Muse' })).toHaveAttribute('aria-valuenow', '42')
    expect(first).toHaveTextContent('Radiohead, Placebo, and 10 more')
    expect(within(first).getByRole('link', { name: 'Radiohead' })).toHaveAttribute('href', '/library/artists/100')
    expect(within(first).getByRole('link', { name: 'Muse on Last.fm' })).toHaveAttribute('href', 'https://www.last.fm/music/Muse')
    expect(screen.getByText('Massive Attack').closest('li')).toHaveTextContent(/similar to 3 of your artistsPortishead/i)

    fireEvent.click(within(first).getByRole('button', { name: 'Hide Muse' }))
    await vi.waitFor(() => expect(requests.some((r) => r.method === 'POST')).toBe(true))
    const posted = requests.find((r) => r.method === 'POST')!
    expect(await posted.clone().json()).toEqual({ key: 'muse', name: 'Muse' })
  })

  it('adds a suggested artist to Lidarr: the right one, and which albums', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <DiscoverPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Add Muse to Lidarr' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add Muse to Lidarr' })
    expect(await within(dialog).findByText('UK rock band · Group')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: /^Monitor/ }))
    fireEvent.click(await within(dialog).findByRole('option', { name: 'Every album' }))
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Look for them now' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add to Lidarr' }))
    expect(await within(dialog).findByText(/Added Muse to Lidarr: it's looking for their albums now/)).toBeInTheDocument()
    const post = requests.find((r) => new URL(r.url).pathname === '/api/lidarr/artists')!
    expect(await post.clone().json()).toEqual({ mbid: '9c9f1380-2516-4fc9-a3e6-f9f61941d090', monitor: 'all', search: true })
  })
})
