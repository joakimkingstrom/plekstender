import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RewindPage } from './RewindPage'

const item = (name: string, artist: string | null, listens: number, colour: string | null = null) => ({
  name, artist, listens, listening_ms: 0, rating_key: null, thumb: null, colour,
})
let asked: URL | null = null
let servers: unknown[] = []
let samples: URL[] = []

beforeEach(() => {
  asked = null
  servers = []
  samples = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/servers') return json(servers)
      if (url.pathname === '/api/servers/1/sync')
        return json({ active: null, last: null, last_sync_at: null, counts: { artists: 1, albums: 1, tracks: 1, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
      if (url.pathname === '/api/servers/1/rate/sample') {
        samples.push(url)
        return json({ playable: true, reason: null, preview: { rating_key: 300, url: '/clip/300.m4a', start_ms: 0, quiet_intro: false } })
      }
      if (url.pathname === '/api/rewind') {
        asked = url
        return json({
          start: '2026-01-01', end: '2026-11-20', listens: 4321, listening_ms: 200 * 3_600_000, days: 290, period_days: 324,
          artists: 410, tracks: 2100,
          top_artists: [{ ...item('Radiohead', null, 300), rating_key: 100 }, item('Portishead', null, 120)],
          top_albums: [item('OK Computer', 'Radiohead', 90, '#2850c8')],
          top_tracks: [{ ...item('Paranoid Android', 'Radiohead', 40, '#c81e1e'), rating_key: 200, track_key: 300 }],
          new_artists: [item('Wet Leg', null, 30)], new_albums: [], new_releases: [],
          weekdays: [1, 2, 3, 9, 4, 5, 6], hours: Array.from({ length: 24 }, (_, h) => (h === 21 ? 50 : 3)),
          genres: [{ genre: 'Rock', listens: 900 }], genre_days: [{ when: '3', genre: 'Rock', share: 0.5 }],
          genre_parts: [{ when: 'evening', genre: 'Trip Hop', share: 0.4 }],
          repeats: [
            { name: 'Glory Box', artist: 'Portishead', runs: 3, longest: 5, shortest: 2 },
            { name: 'Roads', artist: 'Portishead', runs: 2, longest: 3, shortest: 3 },
            { name: 'Sour Times', artist: 'Portishead', runs: 1, longest: 4, shortest: 4 },
          ],
          longest_repeat: { name: 'Glory Box', artist: 'Portishead', runs: 1, longest: 5, shortest: 5 },
          game: null, app_seconds: 5400,
        })
      }
      return json({})
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Rewind', () => {
  it('plays a period as a story of cards', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <RewindPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const year = String(new Date().getFullYear())
    fireEvent.click(screen.getByRole('button', { name: `Play ${year} so far` }))
    const story = await screen.findByRole('dialog', { name: `Rewind: ${year} so far` })
    expect(await screen.findByText('4,321')).toBeInTheDocument()
    expect(asked?.searchParams.get('start')).toBe(`${year}-01-01`)
    expect(story.dataset.colour).toBe('#2850c8') // from the top album's cover

    fireEvent.click(screen.getByRole('button', { name: 'Next →' }))
    expect(screen.getByRole('heading', { name: 'Radiohead' })).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByRole('heading', { name: 'OK Computer' })).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(screen.getByRole('heading', { name: 'Radiohead' })).toBeInTheDocument()
    // On to the end: new obsessions, when, genres, repeats, time in Plekstender, outro.
    for (let i = 0; i < 6; i++) fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByText('On repeat')).toBeInTheDocument()
    expect(screen.getByText('2 to 5 times in a row on three different occasions')).toBeInTheDocument()
    expect(screen.getByText('3 times in a row on two different occasions')).toBeInTheDocument()
    expect(screen.getByText('4 times in a row')).toBeInTheDocument() // once: no need to say so
    for (let i = 0; i < 2; i++) fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByRole('heading', { name: year + ' so far' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('plays the songs and artists as you go, when asked to', async () => {
    servers = [{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }]
    vi.stubGlobal(
      'Audio',
      class extends EventTarget {
        src = ''
        preload = ''
        currentTime = 0
        play() {
          return Promise.resolve()
        }
        pause() {}
      },
    )
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <RewindPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const year = String(new Date().getFullYear())
    fireEvent.click(screen.getByRole('button', { name: `Play ${year} so far` }))
    const music = await screen.findByRole('button', { name: 'Play music' })
    expect(samples).toEqual([]) // off until asked for
    fireEvent.click(music)
    // The intro plays the top song; the next card, the top artist.
    await vi.waitFor(() => expect(samples.map((u) => [u.searchParams.get('mode'), u.searchParams.get('rating_key')])).toEqual([['track', '300']]))
    fireEvent.click(screen.getByRole('button', { name: 'Next →' }))
    await vi.waitFor(() => expect(samples.at(-1)?.searchParams.get('mode')).toBe('artist'))
    expect(samples.at(-1)?.searchParams.get('rating_key')).toBe('100')
    expect(screen.getByRole('button', { name: 'Turn the music off' })).toHaveAttribute('aria-pressed', 'true')
    localStorage.clear()
  })
})
