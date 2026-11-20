import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScrobbleServicesCard } from './ScrobbleServicesCard'

const targets = [
  {
    id: 1, service: 'koito', label: 'Koito', url: 'https://koito.example/apis/listenbrainz', username: 'joakim',
    enabled_at: '2026-11-20T10:00:00Z', sent: 120, pending: 2, last_submitted_at: null, paused: false, error: null,
  },
  {
    id: 2, service: 'librefm', label: 'Libre.fm', url: 'https://libre.fm/2.0/', username: 'Joakim',
    enabled_at: '2026-11-20T10:00:00Z', sent: 5, pending: 9, last_submitted_at: null, paused: true,
    error: 'Libre.fm rejected the token, so scrobbling is paused. Add it again with a new token to resume.',
  },
]  // prettier-ignore

let fetchMock: ReturnType<typeof vi.fn<(input: Request | string) => Promise<Response>>>
const posted = () =>
  fetchMock.mock.calls
    .map(([r]) => r)
    .filter((r): r is Request => typeof r !== 'string' && r.method === 'POST' && new URL(r.url).pathname === '/api/scrobblers')

beforeEach(() => {
  fetchMock = vi.fn(async (input: Request | string) => {
    const req = typeof input === 'string' ? null : input
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    if (req?.method === 'POST') return json({ ...targets[0], id: 3, service: 'maloja', label: 'Maloja' }, 201)
    return json(targets)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderCard() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ScrobbleServicesCard />
    </QueryClientProvider>,
  )
}

describe('More scrobble services', () => {
  it('lists the added services compactly', async () => {
    renderCard()
    expect(await screen.findByText('120 sent · 2 waiting')).toBeInTheDocument()
    expect(screen.getByText(/Libre.fm rejected the token/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in again' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Token')).not.toBeInTheDocument() // no forms until one is picked
  })

  it('adds one picked from the list, showing only its fields', async () => {
    renderCard()
    fireEvent.click(await screen.findByRole('button', { name: /^Add a scrobble service/ }))
    fireEvent.click(screen.getByRole('option', { name: 'Maloja' }))
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Address'), { target: { value: 'https://maloja.example/apis/listenbrainz' } })
    fireEvent.change(screen.getByLabelText('Token'), { target: { value: ' key ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Check and add' }))
    await vi.waitFor(async () =>
      expect(await posted()[0]?.clone().json()).toEqual({
        service: 'maloja',
        url: 'https://maloja.example/apis/listenbrainz',
        token: 'key',
      }),
    )
  })

  it('signs a paused Libre.fm in again', async () => {
    renderCard()
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in again' }))
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'joakim' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 's3cret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in and add' }))
    await vi.waitFor(async () =>
      expect(await posted()[0]?.clone().json()).toEqual({ service: 'librefm', username: 'joakim', password: 's3cret', url: null }),
    )
  })
})
