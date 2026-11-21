import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationBell } from './NotificationBell'

const now = new Date().toISOString()
let posted: string[] = []

beforeEach(() => {
  posted = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      const json = (body: unknown, status = 200) =>
        new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/servers') return json([])
      if (input.method === 'POST') {
        posted.push(`${url.pathname} ${await input.text()}`)
        return json(null, 204)
      }
      return json({
        items: [
          { id: 1, level: 'error', title: 'Koito isn’t working', detail: 'Koito rejected the token.', link: '/settings/scrobbling', instance: false, first_at: now, updated_at: now, resolved_at: null, read: false },
          { id: 2, level: 'warning', title: 'JamBase needs a look', detail: '765 of 900 requests this month', link: '/settings/services/gigs', instance: true, first_at: now, updated_at: now, resolved_at: null, read: true },
          { id: 3, level: 'error', title: 'SearXNG (image search) isn’t working', detail: null, link: null, instance: true, first_at: now, updated_at: now, resolved_at: now, read: false },
        ],
        unread: 1,
      })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('The bell', () => {
  it('counts new problems, lists them with a way to fix, and reads them when opened', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <NotificationBell isAdmin />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const bell = await screen.findByRole('button', { name: 'Notifications: 1 new' })
    fireEvent.click(bell)
    expect(screen.getByText('Koito rejected the token.')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Fix' }).map((a) => a.getAttribute('href'))).toEqual([
      '/settings/scrobbling',
      '/settings/services/gigs',
    ])
    expect(screen.getByText('· fixed')).toBeInTheDocument()
    await vi.waitFor(() => expect(posted).toContain('/api/notifications/read {"ids":[1]}'))

    fireEvent.click(screen.getByRole('button', { name: 'Hide for all admins' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss: Koito isn’t working' }))
    await vi.waitFor(() =>
      expect(posted).toEqual(expect.arrayContaining(['/api/notifications/2/hide ', '/api/notifications/1/dismiss '])),
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByText('Koito rejected the token.')).not.toBeInTheDocument()
  })
})
