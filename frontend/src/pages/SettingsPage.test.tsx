import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { describeSchedule } from '../format'
import { AppearancePage, LibrarySettingsPage } from './SettingsPage'

afterEach(() => vi.unstubAllGlobals())

const responses: Record<string, unknown> = {
  '/api/auth/me': { id: 1, username: 'owner', email: null, thumb_url: null, is_admin: true },
  '/api/admin/library': { album_top_n: 3, artist_top_n: 10, incremental_minutes: 30, full_days: 7 },
  '/api/servers': [],
}

function renderPage() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname
      const body = responses[path]
      return new Response(JSON.stringify(body ?? { detail: 'Not found' }), {
        status: body === undefined ? 404 : 200,
        headers: { 'content-type': 'application/json' },
      })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <LibrarySettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('LibrarySettingsPage', () => {
  it('shows the current values and only allows saving valid changes', async () => {
    renderPage()
    const artist = await screen.findByRole('spinbutton', { name: 'Artist popularity' })
    expect(artist).toHaveValue(10)
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled() // nothing changed yet
    fireEvent.click(screen.getByRole('button', { name: 'Increase Artist popularity' }))
    expect(artist).toHaveValue(11)
    expect(save).toBeEnabled()

    fireEvent.change(artist, { target: { value: '5' } })
    expect(save).toBeEnabled()

    fireEvent.change(artist, { target: { value: '0' } })
    expect(save).toBeDisabled()
    expect(screen.getByText('Between 1 and 50.')).toBeInTheDocument()
  })
})

describe('describeSchedule', () => {
  it('describes the sync schedule in words', () => {
    expect(describeSchedule(30, 7)).toBe('Changes are picked up every 30 minutes, with a full sync weekly.')
    expect(describeSchedule(60, 1)).toBe('Changes are picked up every hour, with a full sync daily.')
    expect(describeSchedule(180, 14)).toBe('Changes are picked up every 3 hours, with a full sync every 14 days.')
  })
})

describe('AppearancePage', () => {
  it('saves the chosen accent colour to the account and applies it', async () => {
    const fetchMock = vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname
      if (path === '/api/auth/me/preferences') {
        const body = await input.clone().json()
        return new Response(JSON.stringify({ ...responses['/api/auth/me'] as object, preferences: body }), {
          headers: { 'content-type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ ...(responses['/api/auth/me'] as object), preferences: { accent: null } }), {
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <AppearancePage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('radio', { name: 'Amber' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('radio', { name: 'Blue' }))
    expect(await screen.findByRole('radio', { name: 'Blue' })).toHaveAttribute('aria-checked', 'true')
    await vi.waitFor(async () => {
      const put = fetchMock.mock.calls.find(([r]) => r.method === 'PUT')
      expect(await put![0].clone().json()).toEqual({ accent: '#3e63dd', skip_threshold: 80, phone_start: 'rate' })
    })
  })
})
