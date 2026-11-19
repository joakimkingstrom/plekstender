import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AlbumFiles, ScanCard } from './MusicFiles'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderWith(ui: ReactNode, responses: Record<string, unknown>) {
  const fetch = vi.fn(async (input: Request) => {
    const key = `${input.method} ${new URL(input.url).pathname}`
    const body = responses[key]
    return new Response(JSON.stringify(body ?? { detail: 'Not found' }), {
      status: body === undefined ? 404 : 200,
      headers: { 'content-type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
  return fetch
}

const plex = (title: string, track: number) => ({
  title,
  artist: 'Radiohead',
  album: 'OK Computer',
  albumartist: 'Radiohead',
  track,
  disc: 1,
  year: 1997,
  genre: ['Rock'],
})

describe('Music files', () => {
  it('shows the last scan and starts another', async () => {
    const fetch = renderWith(<ScanCard serverId={1} />, {
      'GET /api/servers/1/files/summary': {
        mapped: true,
        files: 4210,
        matched: 4188,
        errors: 3,
        active: null,
        last: { id: 2, status: 'succeeded', progress: {}, stats: {}, error: null, created_at: new Date().toISOString(), finished_at: new Date().toISOString() },
      },
      'POST /api/servers/1/files/scans': { id: 3, status: 'queued', progress: {}, stats: {}, error: null, created_at: new Date().toISOString(), finished_at: null },
    })
    expect(await screen.findByText(/4,210 files, 4,188 of them Plex tracks/)).toBeInTheDocument()
    expect(screen.getByText(/3 couldn't be read/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Scan again' }))
    await vi.waitFor(() => expect(fetch.mock.calls.some(([r]) => r.method === 'POST')).toBe(true))
  })

  it("shows an album's file tags, with differences from Plex highlighted", async () => {
    renderWith(<AlbumFiles serverId={1} albumKey={200} />, {
      'GET /api/servers/1/files/albums/200': {
        scanned: true,
        tracks: [
          { rating_key: 300, disc: 1, number: 2, plex: plex('Paranoid Android', 2), file: 'Radiohead/OK Computer/02.flac', format: 'flac', duration_ms: 383_000, has_cover: true, tags: { title: 'Paranoid Android', artist: 'Radiohead', album: 'OK Computer', albumartist: 'Radiohead', track: 2, date: '1997', genre: ['Rock'] }, error: null, differences: [] },
          { rating_key: 301, disc: 1, number: 6, plex: plex('Karma Police', 6), file: 'Radiohead/OK Computer/06.flac', format: 'flac', duration_ms: 261_000, has_cover: false, tags: { title: 'Karma Police (Live)', artist: 'Radiohead', album: 'OK Computer', albumartist: 'Radiohead', track: 6, date: '1997', genre: ['Rock'] }, error: null, differences: ['title'] },
          { rating_key: 303, disc: 1, number: 11, plex: plex('Lucky', 11), file: null, format: null, duration_ms: null, has_cover: false, tags: {}, error: null, differences: [] },
        ],
      },
    })
    expect(await screen.findByText('1 of 3 songs differ from Plex')).toBeInTheDocument()
    const different = screen.getByLabelText('Karma Police (Live) (Plex: Karma Police)')
    expect(different).toBeInTheDocument()
    const rows = within(screen.getByRole('table')).getAllByRole('row')
    expect(rows[3].textContent).toContain('no file found')
  })

  it('points to the scan when the folder has never been scanned', async () => {
    renderWith(<AlbumFiles serverId={1} albumKey={200} />, {
      'GET /api/servers/1/files/albums/200': { scanned: false, tracks: [] },
    })
    expect(await screen.findByRole('link', { name: 'Settings → Music files' })).toHaveAttribute('href', '/settings/files')
  })
})
