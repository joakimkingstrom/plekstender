import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { TastePage } from './TastePage'

const taste = {
  years: [2024, 2025, 2026],
  listens: [100, 200, 50],
  genres: [
    { name: 'Rock', shares: [0.5, 0.25, 0] },
    { name: 'Trip Hop', shares: [0.5, 0.75, 1] },
  ],
  genre_matched: [80, 160, 40],
  decades: [
    { name: '1990s', shares: [1, 0.5, 0.2] },
    { name: '2010s', shares: [0, 0.5, 0.8] },
  ],
  decade_matched: [90, 180, 45],
  median_release: [1997, 2004, 2016],
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname
      const body: Record<string, unknown> = {
        '/api/servers': [{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }],
        '/api/stats/taste': taste,
      }
      return new Response(JSON.stringify(body[path] ?? { detail: 'Not found' }), {
        status: path in body ? 200 : 404,
        headers: { 'content-type': 'application/json' },
      })
    }),
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('shows genres and release decades by year, and how old the music was', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <TastePage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const genres = await screen.findByRole('table', { name: "Share of each year's listens by genre" })
  expect(within(genres).getAllByRole('row').map((r) => r.textContent)).toEqual([
    'Genre202420252026',
    'Rock50%25%0%',
    'Trip Hop50%75%100%',
  ])
  expect(screen.getByText(/Based on 80% of your listens: the rest/)).toBeInTheDocument()
  expect(screen.getByText(/In 2026, half of what you played came out in/).textContent).toBe(
    'In 2026, half of what you played came out in 2016 or earlier (10 years before).',
  )
  expect(screen.getByRole('table', { name: 'Median age of the music played, by year' })).toBeInTheDocument()
  // Hovering a square says what it is.
  const card = screen.getByRole('heading', { name: 'Your genres over the years' }).closest('section')!
  const squares = within(card).getByRole('img').querySelectorAll('span.rounded-\\[3px\\]')
  fireEvent.mouseEnter(squares[1])
  expect(within(card).getByText('Rock · 2025: 25% of 160 listens with a genre')).toBeInTheDocument()
})
