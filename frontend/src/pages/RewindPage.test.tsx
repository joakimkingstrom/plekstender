import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RewindPage } from './RewindPage'

const item = (name: string, artist: string | null, listens: number, colour: string | null = null) => ({
  name, artist, listens, listening_ms: 0, rating_key: null, thumb: null, colour,
})
let asked: URL | null = null

beforeEach(() => {
  asked = null
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/servers') return json([])
      if (url.pathname === '/api/rewind') {
        asked = url
        return json({
          start: '2026-01-01', end: '2026-11-20', listens: 4321, listening_ms: 200 * 3_600_000, days: 290, period_days: 324,
          artists: 410, tracks: 2100,
          top_artists: [item('Radiohead', null, 300), item('Portishead', null, 120)],
          top_albums: [item('OK Computer', 'Radiohead', 90, '#2850c8')],
          top_tracks: [item('Paranoid Android', 'Radiohead', 40, '#c81e1e')],
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
})
