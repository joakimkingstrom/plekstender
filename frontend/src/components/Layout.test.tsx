import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Layout } from './Layout'

const me = { id: 1, username: 'joakim', email: null, thumb_url: null, is_admin: false, preferences: { accent: null, skip_threshold: 80, phone_start: 'rate' } }

function renderAt(path: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<p>Home page</p>} />
            <Route path="/library/rate" element={<p>Rate page</p>} />
          </Route>
          <Route path="/login" element={<p>Login page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function phone(matches: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches, media: query, addEventListener() {}, removeEventListener() {} }))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Layout', () => {
  it("doesn't sign you out when the server can't be reached", async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    renderAt('/')
    expect(await screen.findByRole('heading', { name: /can't be reached|offline/ })).toBeInTheDocument()
    expect(screen.queryByText('Login page')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('opens on Rate on a phone, and on Home elsewhere', async () => {
    const json = () => new Response(JSON.stringify(me), { headers: { 'content-type': 'application/json' } })
    vi.stubGlobal('fetch', vi.fn(async () => json()))
    phone(true)
    renderAt('/')
    expect(await screen.findByText('Rate page')).toBeInTheDocument()
    cleanup()
    phone(false)
    renderAt('/')
    expect(await screen.findByText('Home page')).toBeInTheDocument()
  })
})
