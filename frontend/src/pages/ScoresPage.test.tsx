import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ScoresPage } from './ScoresPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const row = (id: number, name: string, you: boolean, points: number, streak: number, last: number | null) => ({
  user_id: id,
  username: name,
  thumb_url: null,
  you,
  points,
  rounds: 10,
  correct: 7,
  best_streak: streak,
  last_score: last,
  last_rounds: last === null ? null : 4,
  last_played_at: last === null ? null : new Date().toISOString(),
})

describe('ScoresPage', () => {
  it('ranks everyone by points, with best streaks and latest games', async () => {
    const fetch = vi.fn(async (input: Request) => {
      const mode = new URL(input.url).searchParams.get('mode')
      const rows = mode === 'album' ? [row(2, 'friend', false, 54, 2, 54)] : [row(1, 'joakim', true, 163, 7, 48), row(2, 'friend', false, 54, 2, null)]
      return new Response(JSON.stringify(rows), { headers: { 'content-type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetch)
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <ScoresPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getAllByRole('cell').map((c) => c.textContent))).toEqual([
      // (The first letter is the avatar placeholder.)
      ['🥇', 'JjoakimYou', '163', '7', '48 in 4 roundsjust now', '70% of 10'],
      ['🥈', 'Ffriend', '54', '2', '–', '70% of 10'],
    ])
    fireEvent.click(screen.getByRole('button', { name: 'Albums' }))
    await vi.waitFor(() => expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(2))
    expect(fetch.mock.calls.some(([r]) => new URL(r.url).searchParams.get('mode') === 'album')).toBe(true)
  })
})
