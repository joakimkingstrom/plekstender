import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AchievementCelebration, AchievementList } from './Achievements'

const roadie = { id: 'roadie', name: 'Roadie', group: 'library', message: 'You carry the gear now. Lift with your knees.', hint: '100 artists in your library', goal: 100, progress: 120, unlocked_at: '2026-11-20T20:00:00Z', seen: false }
const groupie = { id: 'groupie', name: 'Groupie', group: 'gigs', message: 'Security knows you by name.', hint: '25 gigs been to', goal: 25, progress: 5, unlocked_at: null, seen: false }
let seen: unknown = null

beforeEach(() => {
  seen = null
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      const json = (body: unknown, status = 200) =>
        new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/achievements/seen') {
        seen = await input.json()
        return json(null, 204)
      }
      if (url.searchParams.get('new') === 'true') return json({ enabled: true, items: seen ? [] : [roadie] })
      return json({ enabled: true, items: [roadie, groupie] })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const wrap = (ui: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>)

describe('Achievements', () => {
  it('lists unlocked ones with their message, and locked ones with a hint and progress', async () => {
    wrap(<AchievementList />)
    expect(await screen.findByText('1 of 2 unlocked.')).toBeInTheDocument()
    expect(screen.getByText('You carry the gear now. Lift with your knees.')).toBeInTheDocument()
    expect(screen.queryByText('Groupie')).not.toBeInTheDocument() // a silhouette until unlocked
    expect(screen.getByRole('progressbar', { name: '25 gigs been to: 5 of 25' })).toBeInTheDocument()
  })

  it('celebrates a new one once', async () => {
    wrap(<AchievementCelebration enabled />)
    expect(await screen.findByText('Achievement unlocked')).toBeInTheDocument()
    expect(screen.getByText('Roadie')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Nice!' }))
    await vi.waitFor(() => expect(seen).toEqual({ ids: ['roadie'] }))
    await vi.waitFor(() => expect(screen.queryByText('Achievement unlocked')).not.toBeInTheDocument())
  })
})
