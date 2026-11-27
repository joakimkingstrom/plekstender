import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PlayerWidget } from './PlayerWidget'

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
const joabook = {
  id: 'plexamp-1', name: 'joabook', product: 'Plexamp', platform: 'macOS', version: '4.50.20', on_account: true, on_server: false,
  online: true, direct: true, playing: { title: 'If', artist: 'Glasvegas', album: 'Glasvegas', state: 'playing', rating_key: 301 },
}
const state = {
  reachable: true, detail: null, state: 'playing', position_ms: 61_000, duration_ms: 264_000, rating_key: 301, queue_id: 88, queue_item_id: 902,
  shuffle: false, repeat: 2, controllable: ['playPause', 'skipPrevious', 'skipNext', 'seekTo', 'shuffle', 'repeat'],
  title: 'If', artist: 'Glasvegas', album: 'Glasvegas', thumb: '/library/metadata/200/thumb/1',
}
let playing: object[] = [joabook]
let fetch: ReturnType<typeof vi.fn>

beforeEach(() => {
  localStorage.clear()
  playing = [joabook]
  Object.assign(state, { reachable: true, detail: null })
  fetch = vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname
    if (path === '/api/servers') return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
    if (path === '/api/servers/1/players') return json({ players: playing, direct_allowed: true })
    if (path.endsWith('/state')) return json(state)
    if (path.endsWith('/queue'))
      return json({ current_item_id: 902, items: [
        { item_id: 901, key: '/library/metadata/300', rating_key: 300, title: 'Geraldine', artist: 'Glasvegas', album: 'Glasvegas', duration_ms: 230_000, thumb: null },
        { item_id: 902, key: '/library/metadata/301', rating_key: 301, title: 'If', artist: 'Glasvegas', album: 'Glasvegas', duration_ms: 264_000, thumb: null },
      ] })
    if (path.endsWith('/command')) return json({ ok: true, route: 'direct', detail: 'The player took it (10.10.1.4:32500).' })
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetch)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function show() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <PlayerWidget />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** The body of the nth POST to a path (1-based), once it's been sent. */
async function sent(path: string, nth: number) {
  const posts = () => fetch.mock.calls.filter(([r]) => r.url.endsWith(path) && r.method === 'POST')
  await vi.waitFor(() => expect(posts().length).toBeGreaterThanOrEqual(nth))
  return posts()[nth - 1][0].clone().json()
}

it('shows small while something plays, then opens on the chosen player with its controls and queue', async () => {
  show()
  fireEvent.click(await screen.findByRole('button', { name: /Playing on joabook/ }))
  fireEvent.click(within(screen.getByRole('list', { name: 'Players' })).getByRole('button', { name: /joabook/ }))

  const widget = await screen.findByRole('region', { name: 'Playing on joabook' })
  expect(await within(widget).findByText('If')).toBeInTheDocument()
  expect(within(widget).getAllByText('Glasvegas')).toHaveLength(2)
  expect(within(widget).getByText('4:24')).toBeInTheDocument()

  fireEvent.click(within(widget).getByRole('button', { name: 'Pause' }))
  expect(await sent('/players/plexamp-1/command', 1)).toEqual({ command: 'pause', route: 'auto', value: null, key: null })
  // Repeat goes round: all → off.
  fireEvent.click(within(widget).getByRole('button', { name: 'Repeat: all' }))
  expect(await sent('/players/plexamp-1/command', 2)).toMatchObject({ command: 'repeat', value: 0 })

  fireEvent.click(within(widget).getByRole('button', { name: 'Show the queue' }))
  const queue = await within(widget).findByRole('list', { name: 'Queue' })
  expect(within(queue).getByRole('button', { current: true })).toHaveTextContent('If')
  fireEvent.click(within(queue).getByRole('button', { name: /Geraldine/ }))
  expect(await sent('/players/plexamp-1/command', 3)).toMatchObject({ command: 'skip_to', value: 901, key: '/library/metadata/300' })

  // Minimised, it remembers the player.
  fireEvent.click(within(widget).getByRole('button', { name: 'Minimise' }))
  expect(localStorage.getItem('player-widget')).toBe('plexamp-1')
})

it('stays away while nothing plays', async () => {
  playing = [{ ...joabook, playing: null }]
  show()
  await vi.waitFor(() => expect(fetch.mock.calls.some(([r]) => r.url.endsWith('/players'))).toBe(true))
  expect(screen.queryByRole('button', { name: /Playing on/ })).toBeNull()
})

it('says what to check when the player gives no answer', async () => {
  localStorage.setItem('player-widget', 'plexamp-1')
  Object.assign(state, { reachable: false, detail: 'No answer from joabook (10.10.1.4:32500: no answer (timed out)). Plexamp listens for remote control on port 32500: check that a firewall…' })
  show()
  fireEvent.click(await screen.findByRole('button', { name: /Playing on joabook/ }))
  fireEvent.click(within(screen.getByRole('list', { name: 'Players' })).getByRole('button', { name: /joabook/ }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/firewall/)
})
