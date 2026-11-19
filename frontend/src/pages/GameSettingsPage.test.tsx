import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GameSettingsPage } from './GameSettingsPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('GameSettingsPage', () => {
  it('saves the game settings to the account', async () => {
    const me = { id: 1, username: 'joakim', email: null, thumb_url: null, is_admin: false, preferences: { accent: null, skip_threshold: 80, phone_start: 'rate' } }
    const fetch = vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (path === '/api/auth/me') return json(me)
      if (path === '/api/servers')
        return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      if (path === '/api/servers/1/game/playlists') return json(['Road trip'])
      if (path === '/api/auth/me/preferences') return json({ ...me, preferences: await input.clone().json() })
      return json({})
    })
    vi.stubGlobal('fetch', fetch)
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <GameSettingsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: '20 s' }))
    fireEvent.click(screen.getByRole('button', { name: 'Deep cuts' }))
    fireEvent.click(screen.getByRole('button', { name: "Don't tell me" }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Playlist name' }), { target: { value: 'Road trip' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await vi.waitFor(async () => {
      const put = fetch.mock.calls.find(([r]) => r.method === 'PUT')
      expect((await put![0].clone().json()).game).toEqual({
        round_seconds: 20,
        max_guesses: 3,
        artist_feedback: false,
        playlist: 'Road trip',
        popularity: 'deep',
      })
    })
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
  })
})
