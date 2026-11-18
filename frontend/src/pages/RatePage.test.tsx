import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RatePage } from './RatePage'

const song = (key: number, title: string) => ({
  rating_key: key,
  kind: 'track',
  title,
  subtitle: 'Radiohead',
  album_title: 'OK Computer',
  album_rating_key: 200,
  thumb: null,
  year: 1997,
  duration_ms: 240_000,
  popularity: 1000,
  summary: null,
  critic_rating: null,
  user_rating: null,
  plays: 3,
})

let queuePages: unknown[][] = []
let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>

beforeEach(() => {
  try {
    localStorage.clear()
  } catch {
    // no storage in this environment
  }
  queuePages = [[song(1, 'Airbag'), song(2, 'Paranoid Android')], []]
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    const path = new URL(input.url).pathname
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    if (path === '/api/servers')
      return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
    if (path === '/api/servers/1/sync')
      return json({ active: null, last: null, last_sync_at: null, counts: { artists: 1, albums: 1, tracks: 2, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
    if (path === '/api/servers/1/rate/queue') return json(queuePages.shift() ?? [])
    if (path === '/api/servers/1/ratings') {
      const body = await input.clone().json()
      return json({ rating_key: body.rating_key, item_type: 'track', user_rating: body.rating })
    }
    if (path === '/api/servers/1/ratings/undo') return json({ rating_key: 1, item_type: 'track', user_rating: null })
    return json({ detail: 'Not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <RatePage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function ratingsPosted() {
  const calls = fetchMock.mock.calls.filter(([r]) => new URL(r.url).pathname === '/api/servers/1/ratings')
  return Promise.all(calls.map(([r]) => r.clone().json()))
}

describe('RatePage', () => {
  it('rates the card with the stars, moves on, and undo brings it back', async () => {
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Airbag' })).toBeInTheDocument()
    expect(screen.getByText('Radiohead · OK Computer · 1997')).toBeInTheDocument()

    fireEvent.keyDown(screen.getByRole('slider', { name: 'Rate Airbag' }), { key: '4' })
    expect(await screen.findByRole('heading', { name: 'Paranoid Android' })).toBeInTheDocument()
    await vi.waitFor(async () => expect(await ratingsPosted()).toEqual([{ rating_key: 1, rating: 8 }]))

    fireEvent.click(screen.getByRole('button', { name: /Undo/ }))
    expect(await screen.findByRole('heading', { name: 'Airbag' })).toBeInTheDocument()
  })

  it('lists songs on the desktop layout and rates them with the keyboard', async () => {
    localStorage.setItem('plekstender.rate', JSON.stringify({ layout: 'desktop' }))
    renderPage()
    const second = await screen.findByRole('slider', { name: 'Rate Paranoid Android' })
    fireEvent.keyDown(second, { key: '3', shiftKey: true }) // 2½ stars
    await vi.waitFor(async () => expect(await ratingsPosted()).toEqual([{ rating_key: 2, rating: 5 }]))
    expect(screen.getByText(/1 of 2 rated/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Next 2 songs' }))
    expect(await screen.findByText(/Nothing left to rate here/)).toBeInTheDocument()
  })

  it('asks for a fresh queue when the selection changes', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Airbag' })
    queuePages = [[{ ...song(9, 'OK Computer'), kind: 'album', subtitle: 'Radiohead' }]]
    fireEvent.click(screen.getByRole('button', { name: 'Albums' }))
    expect(await screen.findByRole('heading', { name: 'OK Computer' })).toBeInTheDocument()
    const bodies = await Promise.all(
      fetchMock.mock.calls
        .filter(([r]) => new URL(r.url).pathname === '/api/servers/1/rate/queue')
        .map(([r]) => r.clone().json()),
    )
    expect(bodies.at(-1)).toMatchObject({ kind: 'album', pool: 'listened', exclude: [] })
  })
})
