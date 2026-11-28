import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CoverSongsCard } from './CoverSongsCard'

let state: Record<string, unknown>
let puts: unknown[]

beforeEach(() => {
  state = { enabled: false, secondhandsongs: true, musicbrainz: true, api_key: false, per_hour: 90, per_day: 900, songs: 5000, checked: 0, covers: 0, requests_left: 4000 }
  puts = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (input.method === 'PUT') {
        const body = await input.clone().json()
        puts.push(body)
        const key = body.remove_key ? false : Boolean(body.api_key) || state.api_key
        state = { ...state, enabled: body.enabled, secondhandsongs: body.secondhandsongs, musicbrainz: body.musicbrainz, api_key: key, per_hour: key ? 900 : 90, per_day: key ? 9000 : 900, checked: 120, covers: 7 }
      }
      return json(state)
    }),
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('turns cover song lookups on, shows progress, and saves an optional key', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CoverSongsCard />
    </QueryClientProvider>,
  )
  const on = await screen.findByRole('checkbox', { name: /Look up cover songs/ })
  expect(on).not.toBeChecked()
  fireEvent.click(on)
  await vi.waitFor(() => expect(puts).toEqual([{ enabled: true, secondhandsongs: true, musicbrainz: true, remove_key: false }]))
  expect(await screen.findByText(/120 of 5,000 songs looked up, 7 covers found/)).toBeInTheDocument()
  // 4,000 requests (two an artist, one a cover) at 900 a day.
  expect(screen.getByText(/About 5 more days on SecondHandSongs, at up to 900 requests a day/)).toBeInTheDocument()

  fireEvent.change(screen.getByLabelText(/SecondHandSongs API key/), { target: { value: 'ed0a8c61-4520-4317-9b95-7a260260b4c0' } })
  fireEvent.click(screen.getByRole('button', { name: 'Check and save' }))
  await vi.waitFor(() =>
    expect(puts[1]).toEqual({ enabled: true, secondhandsongs: true, musicbrainz: true, api_key: 'ed0a8c61-4520-4317-9b95-7a260260b4c0', remove_key: false }),
  )
  expect(await screen.findByText(/SecondHandSongs accepted the key/)).toBeInTheDocument()
  expect(screen.getByText(/About 11 more hours on SecondHandSongs/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
  await vi.waitFor(() => expect(puts[2]).toEqual({ enabled: true, secondhandsongs: true, musicbrainz: true, remove_key: true }))
  // MusicBrainz only: no SecondHandSongs key to give.
  fireEvent.click(screen.getByRole('checkbox', { name: /^SecondHandSongs/ }))
  await vi.waitFor(() => expect(puts[3]).toMatchObject({ secondhandsongs: false, musicbrainz: true }))
  await vi.waitFor(() => expect(screen.queryByLabelText(/SecondHandSongs API key/)).not.toBeInTheDocument())
})
