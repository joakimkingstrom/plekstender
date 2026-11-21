import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PlaylistImport } from './PlaylistImport'

const preview = {
  name: 'Road trip',
  total: 3,
  matched: 2,
  songs: [
    { index: 0, title: 'Karma Police', artist: 'Radiohead', album: 'OK Computer', duration_ms: 264000,
      match: { rating_key: 301, title: 'Karma Police', artist: 'Radiohead', album: 'OK Computer', duration_ms: 264000, how: 'exact' } },
    { index: 1, title: 'Glory Box', artist: 'Portishead', album: null, duration_ms: 301000,
      match: { rating_key: 304, title: 'Glory Box', artist: 'Portishead', album: 'Dummy', duration_ms: 301000, how: 'title' } },
    { index: 2, title: 'Elsewhere', artist: 'Nobody', album: 'X', duration_ms: null, match: null },
  ],
}  // prettier-ignore

let fetchMock: ReturnType<typeof vi.fn<(input: Request | string, init?: RequestInit) => Promise<Response>>>

beforeEach(() => {
  fetchMock = vi.fn(async (input: Request | string) => {
    const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    if (url.pathname.endsWith('/import/preview')) return json(preview)
    if (url.pathname.endsWith('/import/spotify')) return json({ ...preview, name: 'Sunday' })
    if (url.pathname.endsWith('/import/tidal')) return json({ ...preview, name: 'Tidal Sunday' })
    if (url.pathname.endsWith('/playlists/import')) return json({ plex_playlist_key: 9, songs: 1 }, 201)
    return json({ detail: 'Not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Importing a playlist', () => {
  it('previews the matches, lets songs be left out, and makes it in Plex', async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PlaylistImport serverId={1} />
      </QueryClientProvider>,
    )
    const input = screen.getByLabelText('A playlist file')
    fireEvent.change(input, { target: { files: [new File(['csv'], 'Road trip.csv', { type: 'text/csv' })] } })
    expect(await screen.findByText(/of 3 songs are in your library/)).toBeInTheDocument()
    expect(String(fetchMock.mock.calls[0][0])).toContain('filename=Road%20trip.csv')
    expect(screen.getByText('likely')).toBeInTheDocument()
    expect(screen.getByText('Nobody – Elsewhere (X)')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Road trip')

    fireEvent.click(screen.getByRole('checkbox', { name: 'Include Glory Box' }))
    fireEvent.click(screen.getByRole('button', { name: 'Make it in Plex (1 song)' }))
    expect(await screen.findByText('Made “Road trip” in Plex with 1 song.')).toBeInTheDocument()
    const sent = fetchMock.mock.calls.map(([r]) => r).find((r): r is Request => typeof r !== 'string')!
    expect(await sent.clone().json()).toEqual({ name: 'Road trip', rating_keys: [301] })
  })

  it('reads a Spotify playlist link the same way', async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PlaylistImport serverId={1} />
      </QueryClientProvider>,
    )
    fireEvent.change(screen.getByLabelText('Spotify or Tidal playlist link'), { target: { value: ' https://open.spotify.com/playlist/abc ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Read it' }))
    expect(await screen.findByRole('textbox', { name: 'Name' })).toHaveValue('Sunday')
    const sent = fetchMock.mock.calls.map(([r]) => r).find((r): r is Request => typeof r !== 'string')!
    expect(await sent.clone().json()).toEqual({ url: 'https://open.spotify.com/playlist/abc' })
  })

  it('reads a Tidal link from Tidal', async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PlaylistImport serverId={1} />
      </QueryClientProvider>,
    )
    const url = 'https://tidal.com/browse/playlist/0d5165ae-81e3-4864-ab7c-2cd0b03f3f1b'
    fireEvent.change(screen.getByLabelText('Spotify or Tidal playlist link'), { target: { value: url } })
    fireEvent.click(screen.getByRole('button', { name: 'Read it' }))
    expect(await screen.findByRole('textbox', { name: 'Name' })).toHaveValue('Tidal Sunday')
    const sent = fetchMock.mock.calls.map(([r]) => r).find((r): r is Request => typeof r !== 'string')!
    expect(new URL(sent.url).pathname).toBe('/api/servers/1/playlists/import/tidal')
    expect(await sent.clone().json()).toEqual({ url })
  })
})
