import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('LoginPage', () => {
  it('offers Plex sign-in when signed out', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'Not signed in' }), { status: 401 })),
    )
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <LoginPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('button', { name: 'Sign in with Plex' })).toBeInTheDocument()
  })

  it('asks a new instance for the setup code from the log', async () => {
    const requests: Request[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: Request) => {
        requests.push(input)
        const path = new URL(input.url).pathname
        const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
        if (path === '/api/auth/setup') return json({ claimed: false })
        if (path === '/api/auth/plex/pin') return json({ detail: 'That isn’t this Plekstender’s setup code.' }, 403)
        return json({ detail: 'Not signed in' }, 401)
      }),
    )
    vi.stubGlobal('open', vi.fn(() => null))
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <LoginPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const field = await screen.findByLabelText('Setup code')
    expect(screen.getByText(/docker compose logs frontend/)).toBeInTheDocument()
    const button = screen.getByRole('button', { name: 'Sign in with Plex' })
    expect(button).toBeDisabled()
    fireEvent.change(field, { target: { value: ' k7qf-2mxa-9wrt ' } })
    fireEvent.click(button)
    expect(await screen.findByText('That isn’t this Plekstender’s setup code.')).toBeInTheDocument()
    const pin = requests.find((r) => new URL(r.url).pathname === '/api/auth/plex/pin')!
    expect(await pin.clone().json()).toEqual({ setup_code: 'k7qf-2mxa-9wrt' })
  })
})
