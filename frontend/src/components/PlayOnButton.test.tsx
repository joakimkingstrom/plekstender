import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { PlayOnButton } from './PlayOnButton'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
const player = (id: string, name: string, extra: object = {}) => ({
  id, name, product: 'Plexamp', platform: 'macOS', version: '4.50', on_account: true, on_server: false, online: true, direct: true, playing: null, ...extra,
})

it('plays an album on the player chosen, and says when a player gives no answer', async () => {
  let answer = { ok: true, route: 'direct', detail: 'The player took it (10.10.1.4:32500).' }
  const fetch = vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname
    if (path === '/api/servers/1/players') return json({ players: [player('mac', 'joabook'), player('phone', 'joaPhone', { online: false })], direct_allowed: true })
    if (path.endsWith('/play')) return json(answer)
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PlayOnButton serverId={1} ratingKey={200} title="Castaways and Cutouts" />
    </QueryClientProvider>,
  )
  // Your players are only asked for once the menu opens.
  expect(fetch).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Play Castaways and Cutouts on…' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: /joabook/ }))
  expect(await screen.findByRole('status')).toHaveTextContent('Playing on joabook')
  const sent = fetch.mock.calls.find(([r]) => r.url.endsWith('/players/mac/play'))![0]
  expect(await sent.clone().json()).toEqual({ rating_key: 200, route: 'auto' })

  answer = { ok: false, route: 'direct', detail: 'No answer from joaPhone (…). Plexamp listens for remote control on port 32500: check that a firewall…' }
  fireEvent.click(screen.getByRole('button', { name: 'Play Castaways and Cutouts on…' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: /joaPhone/ }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/firewall/)
})
