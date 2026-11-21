import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AchievementCelebration, AchievementList } from './Achievements'

const roadie = { id: 'roadie', name: 'Roadie', group: 'library', message: 'You carry the gear now. Lift with your knees.', hint: '100 artists in your library', goal: 100, progress: 120, unlocked_at: '2026-11-20T20:00:00Z', seen: false }
const frontRow = { id: 'front-row', name: 'Front Row', group: 'gigs', message: 'Your ears are still ringing. Good.', hint: 'Your first gig', goal: 1, progress: 3, unlocked_at: '2026-11-19T20:00:00Z', seen: true }
const groupie = { id: 'groupie', name: 'Groupie', group: 'gigs', message: 'Security knows you by name.', hint: '25 gigs', goal: 25, progress: 3, unlocked_at: null, seen: false }
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
      return json({ enabled: true, items: [roadie, frontRow, groupie] })
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
  it('shows the collected cards in a stack, and only a count of the rest', async () => {
    wrap(<AchievementList />)
    const stack = await screen.findByRole('group', { name: 'Your achievement cards' })
    expect(screen.getByText(/You've collected/)).toHaveTextContent("You've collected 2 of 3 cards.")
    expect(screen.queryByText('Groupie')).not.toBeInTheDocument() // not collected: just counted
    // Newest on top, with what it was for and its number in the set.
    const top = () => screen.getByRole('article', { hidden: false })
    expect(top()).toHaveAccessibleName('Roadie')
    expect(top()).toHaveTextContent('✓ 100 artists in your library')
    expect(top()).toHaveTextContent('01/3')
    // The scroll wheel sends it to the bottom.
    fireEvent.wheel(stack, { deltaY: 120 })
    expect(top()).toHaveAccessibleName('Front Row')
    fireEvent.click(screen.getByRole('button', { name: 'Next card' }))
    expect(top()).toHaveAccessibleName('Roadie')
    fireEvent.keyDown(stack, { key: 'ArrowLeft' })
    expect(top()).toHaveAccessibleName('Front Row')
    expect(screen.getByText('2 / 2')).toBeInTheDocument()
  })

  it('celebrates a new one once', async () => {
    wrap(<AchievementCelebration enabled />)
    expect(await screen.findByText('Achievement unlocked')).toBeInTheDocument()
    expect(screen.getByText('Roadie')).toBeInTheDocument()
    expect(screen.getByText('✓ 100 artists in your library')).toBeInTheDocument() // what it's for
    fireEvent.click(screen.getByRole('button', { name: 'Nice!' }))
    await vi.waitFor(() => expect(seen).toEqual({ ids: ['roadie'] }))
    await vi.waitFor(() => expect(screen.queryByText('Achievement unlocked')).not.toBeInTheDocument())
  })
})
