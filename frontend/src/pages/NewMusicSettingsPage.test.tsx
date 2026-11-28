import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import { NewMusicSettingsPage } from './NewMusicSettingsPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })

it('turns on what happens to new music, and says what it needs', async () => {
  let state = { logos: false, covers: false, lyrics: false, lyrics_write: null as unknown, since: null as string | null, fanart_ready: false, folder_ready: true, tag_editing: false }
  const puts: unknown[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname
      if (path === '/api/servers') return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      if (path === '/api/servers/1/new-music' && input.method === 'PUT') {
        const body = await input.clone().json()
        puts.push(body)
        state = { ...state, ...body, since: new Date().toISOString() }
      }
      if (path === '/api/servers/1/new-music') return json(state)
      return new Response('{}', { status: 404 })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <NewMusicSettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  fireEvent.click(await screen.findByRole('checkbox', { name: /Logos for new artists/ }))
  expect(await screen.findByText(/Needs fanart.tv/)).toBeInTheDocument()
  expect(puts.at(-1)).toEqual({ logos: true, covers: false, lyrics: false, lyrics_write: null })
  expect(await screen.findByText(/Counting music added to Plex since/)).toBeInTheDocument()

  // Lyrics: written next to the songs unless changed (and only once tag editing is on).
  fireEvent.click(screen.getByRole('checkbox', { name: /Lyrics for new albums/ }))
  await vi.waitFor(() => expect(puts.at(-1)).toMatchObject({ lyrics: true, lyrics_write: { sidecar: true, embedded: false } }))
  expect(await screen.findByText(/Tag editing is off for this server/)).toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: /A .lrc \(synced\) or .txt next to each song/ })).toBeChecked()
})
