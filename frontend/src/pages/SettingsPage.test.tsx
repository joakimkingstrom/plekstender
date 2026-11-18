import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { describeSchedule } from '../format'
import { LibrarySettingsPage } from './SettingsPage'

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
    const artist = await screen.findByLabelText(/Artist popularity/)
    expect(artist).toHaveValue(10)
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled() // nothing changed yet

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
