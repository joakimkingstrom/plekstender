import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import { PlayersPage } from './PlayersPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
const mac = {
  id: 'plexamp-1', name: 'Plexamp (Mac)', product: 'Plexamp', platform: 'macOS', version: '4.12.0', on_account: true, on_server: true,
  online: true, direct: true, playing: { title: 'Karma Police', artist: 'Radiohead', album: 'OK Computer', state: 'paused', rating_key: 301 },
}
const phone = { ...mac, id: 'iphone', name: "Joakim's iPhone", product: 'Plex for iOS', platform: 'iOS', on_account: false, direct: false, playing: null }

it('lists players and sends them commands, saying what happened', async () => {
  const fetch = vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname
    if (path === '/api/servers') return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
    if (path === '/api/servers/1/players') return json({ players: [mac, phone], direct_allowed: true })
    if (path === '/api/servers/1/library/recent') return json([{ rating_key: 200, title: 'OK Computer', subtitle: 'Radiohead', thumb: null, added_at: null }])
    if (path.endsWith('/command')) return json({ ok: false, route: 'direct', detail: "The player didn't take it: 192.168.1.20:32500: HTTP 404." })
    if (path.endsWith('/play')) return json({ ok: true, route: 'server', detail: 'The server passed it on.' })
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <PlayersPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const [first, second] = await screen.findAllByRole('listitem')
  expect(within(first).getByText(/Karma Police/)).toBeInTheDocument()
  expect(within(first).getByText('Paused:')).toBeInTheDocument()
  // Only players with an address can be sent to straight.
  expect(within(second).queryByRole('button', { name: 'Straight to it' })).toBeNull()

  fireEvent.click(within(first).getByRole('button', { name: 'Straight to it' }))
  fireEvent.click(within(first).getByRole('button', { name: 'Pause on Plexamp (Mac)' }))
  expect(await within(first).findByText(/Straight to it: The player didn't take it/)).toBeInTheDocument()
  const sent = fetch.mock.calls.find(([r]) => r.url.endsWith('/players/plexamp-1/command'))![0]
  expect(await sent.clone().json()).toEqual({ command: 'pause', route: 'direct', value: null, key: null })

  fireEvent.click(await within(second).findByRole('button', { name: 'Play OK Computer' }))
  expect(await within(second).findByText('Through the server: The server passed it on.')).toBeInTheDocument()
  const play = fetch.mock.calls.find(([r]) => r.url.endsWith('/players/iphone/play'))![0]
  expect(await play.clone().json()).toEqual({ rating_key: 200, route: 'auto' })
})
