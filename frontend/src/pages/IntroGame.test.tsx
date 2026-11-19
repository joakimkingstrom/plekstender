import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { RatePage } from './RatePage'

/** jsdom can't play audio: a stand-in that "loads" and "plays" at once. */
class FakeAudio extends EventTarget {
  src = ''
  preload = ''
  currentTime = 0
  paused = true
  play() {
    this.paused = false
    return Promise.resolve()
  }
  pause() {
    this.paused = true
    this.dispatchEvent(new Event('pause'))
  }
}

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
const preview = (key: number) => ({ rating_key: key, url: `/clip/${key}.m4a`, start_ms: 1700, quiet_intro: false })
const radiohead = { rating_key: 100, kind: 'artist', title: 'Radiohead', subtitle: null, thumb: null }
const trackHit = (key: number, title: string) => ({ rating_key: key, kind: 'track', title, subtitle: 'Radiohead', thumb: null })

let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>

beforeAll(() => {
  vi.stubGlobal('Audio', FakeAudio)
})

beforeEach(() => {
  localStorage.setItem('plekstender.rate', JSON.stringify({ layout: 'swipe', game: true, kind: 'track' }))
  localStorage.setItem('plekstender.rate.swiped', '1')
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    const url = new URL(input.url)
    const path = url.pathname
    const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
    if (path === '/api/servers')
      return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
    if (path === '/api/servers/1/sync')
      return json({ active: null, last: null, last_sync_at: null, counts: { artists: 1, albums: 1, tracks: 2, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
    if (path === '/api/servers/1/rate/queue') return json([song(1, 'Airbag'), song(2, 'Paranoid Android')])
    if (path === '/api/servers/1/library/tags') return json([])
    if (path === '/api/servers/1/game/round') {
      const key = Number(url.searchParams.get('rating_key'))
      return json({ playable: true, reason: null, preview: preview(key) })
    }
    if (path === '/api/servers/1/game/search') {
      const kind = url.searchParams.get('kind')
      if (kind === 'artist') return json([radiohead])
      if (url.searchParams.get('artist') === '100') return json([trackHit(1, 'Airbag'), trackHit(3, 'Lucky')])
      return json([])
    }
    if (path === '/api/servers/1/game/check') {
      const body = await input.clone().json()
      return json({ correct: body.guess === body.target })
    }
    if (path === '/api/servers/1/rate/sample') {
      const key = Number(url.searchParams.get('rating_key'))
      return json({ playable: true, reason: null, preview: preview(key * 10) })
    }
    if (path === '/api/game/stats') return json([])
    if (path === '/api/game/results') {
      const body = await input.clone().json()
      return json({ mode: body.mode, rounds: 1, correct: 1, points: body.points, best_streak: body.streak })
    }
    return json({ detail: 'Not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.unstubAllGlobals()
  vi.stubGlobal('Audio', FakeAudio)
})

async function posted(path: string) {
  const calls = fetchMock.mock.calls.filter(([r]) => new URL(r.url).pathname === path && r.method === 'POST')
  return Promise.all(calls.map(([r]) => r.clone().json()))
}

describe('Guess the intro', () => {
  it('hides the card, takes guesses (wrong ones cost time) and reveals it on the right one', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <RatePage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    // The song plays with the card hidden; no stars until it's answered.
    expect(await screen.findByRole('heading', { name: '? ? ?' })).toBeInTheDocument()
    expect(screen.queryByRole('slider', { name: 'Rate Airbag' })).not.toBeInTheDocument()
    await screen.findByText(/^\d+ s$/)

    // Pick the artist, then one of their songs.
    const box = screen.getByRole('searchbox', { name: 'Your guess' })
    fireEvent.change(box, { target: { value: 'rad' } })
    fireEvent.click(await screen.findByRole('button', { name: /Radiohead/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Lucky/ }))
    expect(await screen.findByText('Not “Lucky”: −3 s')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: /Airbag/ }))

    expect(await screen.findByText(/Correct! \+\d+/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Airbag' })).toBeInTheDocument()
    expect(screen.getByRole('slider', { name: 'Rate Airbag' })).toBeInTheDocument()
    await vi.waitFor(async () => {
      const [result] = await posted('/api/game/results')
      expect(result).toMatchObject({ mode: 'track', correct: true, streak: 1 })
      expect(result.points).toBeGreaterThan(20)
    })
    // The next card's song was prepared while this one played.
    const rounds = fetchMock.mock.calls.map(([r]) => new URL(r.url)).filter((u) => u.pathname === '/api/servers/1/game/round')
    expect(rounds.map((u) => u.searchParams.get('rating_key'))).toEqual(expect.arrayContaining(['1', '2']))

    // Next card: a new round.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    })
    expect(await screen.findByRole('heading', { name: '? ? ?' })).toBeInTheDocument()
  })
})

describe('Play songs (while rating)', () => {
  it("plays each card's song as it comes up and prepares the next one", async () => {
    localStorage.setItem('plekstender.rate', JSON.stringify({ layout: 'swipe', autoplay: true, kind: 'album' }))
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <RatePage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    // The card is shown as usual (no game), with its song playing.
    expect(await screen.findByRole('heading', { name: 'Airbag' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Stop Airbag' })).toBeInTheDocument()
    const samples = () =>
      fetchMock.mock.calls
        .map(([r]) => new URL(r.url))
        .filter((u) => u.pathname === '/api/servers/1/rate/sample')
        .map((u) => `${u.searchParams.get('mode')}:${u.searchParams.get('rating_key')}`)
    expect(samples()).toEqual(expect.arrayContaining(['album:1', 'album:2']))
    expect(screen.getByRole('button', { name: '▶ Play songs' })).toHaveAttribute('aria-pressed', 'true')
  })
})
