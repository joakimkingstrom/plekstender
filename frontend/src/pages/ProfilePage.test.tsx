import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProfilePage } from './ProfilePage'

let saved: unknown = null

beforeEach(() => {
  saved = null
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      const me = {
        id: 1, username: 'joakim', email: null, thumb_url: null, is_admin: true,
        preferences: { accent: null, skip_threshold: 80, phone_start: 'rate', theme: null, achievements: true, instance_notifications: true },
      }
      if (url.pathname === '/api/auth/me/preferences') {
        saved = await input.json()
        return json({ ...me, preferences: saved })
      }
      if (url.pathname === '/api/auth/me') return json(me)
      if (url.pathname === '/api/auth/me/profile')
        return json({
          username: 'joakim', email: null, thumb_url: null, is_admin: true, joined_at: '2026-10-01T10:00:00Z',
          servers: [{ name: 'Home', is_owner: true }], scrobbles: 123456, first_listen_at: '2008-03-01T10:00:00Z', ratings: 5000, gigs: 42,
        })
      return json({})
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Profile', () => {
  it('shows who you are, your numbers, and saves preferences to the account', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <ProfilePage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('heading', { name: 'joakim' })).toBeInTheDocument()
    expect(screen.getByText('123,456')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Gigs been to/ })).toHaveAttribute('href', '/gigs/been-to')
    expect(screen.getByText('Yours')).toBeInTheDocument()
    expect(screen.getByText('Accent colour')).toBeInTheDocument() // Appearance lives here now

    fireEvent.click(screen.getByRole('button', { name: 'Light' }))
    await vi.waitFor(() => expect(saved).toMatchObject({ theme: 'light' }))
    fireEvent.click(screen.getByLabelText(/^Achievements/))
    await vi.waitFor(() => expect(saved).toMatchObject({ achievements: false }))
  })
})
