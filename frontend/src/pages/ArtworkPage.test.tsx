import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { ArtworkPage } from './ArtworkPage'
import { ExportPage } from './ExportPage'

const radiohead = { rating_key: 100, title: 'Radiohead', thumb: null, status: 'none', source: null, applied_at: null, has_mbid: true }
let owner = true
let fetchMock: ReturnType<typeof vi.fn<(input: Request | string, init?: RequestInit) => Promise<Response>>>

beforeEach(() => {
  owner = true
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:preview', revokeObjectURL: () => undefined }))
  fetchMock = vi.fn<(input: Request | string, init?: RequestInit) => Promise<Response>>(async (input) => {
    const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    switch (url.pathname) {
      case '/api/auth/me':
        return json({ id: 1, username: 'owner', email: null, thumb_url: null, is_admin: true })
      case '/api/servers':
        return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: owner, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      case '/api/servers/1/sync':
        return json({ active: null, last: null, last_sync_at: null, counts: { artists: 1, albums: 1, tracks: 2, rated_tracks: 0, plays: 0 }, incremental_minutes: 30, full_days: 7 })
      case '/api/servers/1/artwork/artists':
        return json({ items: [radiohead], total: 1, counts: { none: 1 } })
      case '/api/servers/1/artwork/artists/100/candidates':
        return json({
          candidates: [{ token: 'tok-1', thumb: '/api/servers/1/artwork/image?t=tok-1', label: 'HD logo · 30 likes', likes: 30, width: null, height: null }],
          note: null,
        })
      case '/api/servers/1/artwork/artists/100/preview':
        return new Response(new Blob(['jpeg']), { headers: { 'content-type': 'image/jpeg' } })
      case '/api/servers/1/artwork/artists/bulk-status':
        return json({ changed: 1 })
      case '/api/servers/1/artwork/artists/100/status':
        return new Response(null, { status: 204 })
      case '/api/servers/1/artwork/jobs':
        return json([])
      case '/api/servers/1/artwork/imports':
        return json({
          import_id: 'abc',
          matched: 1,
          total: 2,
          entries: [
            { index: 0, artist_name: 'Radiohead', mbid: 'x', image: '/img/0', match: { rating_key: 100, title: 'Radiohead', thumb: null, status: 'none' }, matched_by: 'mbid' },
            { index: 1, artist_name: 'Unknown Band', mbid: null, image: '/img/1', match: null, matched_by: null },
          ],
        })
      case '/api/servers/1/artwork/imports/abc/apply':
        return json({ id: 5, kind: 'import', status: 'queued', progress: {}, stats: {}, error: null, created_at: '2026-11-19T00:00:00Z', finished_at: null, download: null })
      case '/api/servers/1/artwork/folder':
        return json({ enabled: false, plex_prefix: null, local_prefix: null, plex_root: '/music', example: null })
      case '/api/servers/1/artwork/artists/100/apply':
        return json({ ...radiohead, status: 'done', source: 'fanart', applied_at: '2026-11-18T12:00:00Z' })
      default:
        return json({ detail: 'Not found' }, 404)
    }
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderPage(page: ReactNode = <ArtworkPage />) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        {page}
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function bodiesFor(path: string) {
  return fetchMock.mock.calls
    .filter(([input]) => new URL(typeof input === 'string' ? input : input.url, 'http://localhost').pathname === path)
    .map(([input, init]) => (typeof input === 'string' ? JSON.parse(String(init?.body)) : null))
}

describe('ArtworkPage', () => {
  it('picks a fanart.tv logo, previews it and saves it to Plex', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Radiohead' }))
    fireEvent.click(await screen.findByRole('button', { name: 'HD logo · 30 likes' }))
    expect(await screen.findByAltText('New image for Radiohead')).toHaveAttribute('src', 'blob:preview')
    expect(bodiesFor('/api/servers/1/artwork/artists/100/preview')[0]).toEqual({
      source: { kind: 'candidate', token: 'tok-1' },
      transform: expect.objectContaining({ fit: true, invert: false }),
    })

    fireEvent.click(screen.getByRole('checkbox', { name: 'Invert' }))
    await vi.waitFor(() =>
      expect(bodiesFor('/api/servers/1/artwork/artists/100/preview').at(-1)?.transform.invert).toBe(true),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Save to Plex' }))
    expect(await screen.findByText('Saved to Plex.')).toBeInTheDocument()
  })

  it('exports the library and history, and previews a logo pack and applies the matched artists (Export / import)', async () => {
    renderPage(<ExportPage />)
    expect(await screen.findByRole('link', { name: 'Songs (CSV)' })).toHaveAttribute('href', '/api/servers/1/export/tracks.csv')
    expect(screen.getByRole('link', { name: 'Download all listens' })).toHaveAttribute('href', '/api/listens/export.jsonl')
    const input = await screen.findByLabelText('Import a pack (.zip)')
    fireEvent.change(input, { target: { files: [new File(['zip'], 'pack.zip', { type: 'application/zip' })] } })
    expect(await screen.findByText(/1 of 2 match your artists/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Import Radiohead' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Import Unknown Band' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Apply 1' }))
    await vi.waitFor(async () => {
      const call = fetchMock.mock.calls.find(([input]) => typeof input !== 'string' && input.url.endsWith('/imports/abc/apply'))
      expect(call).toBeTruthy()
      expect(await (call![0] as Request).clone().json()).toEqual({ items: [{ index: 0, rating_key: 100 }] })
    })
  })

  it('cycles an artist’s status from its dot', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Radiohead: To do. Change to Done' }))
    await vi.waitFor(async () => {
      const call = fetchMock.mock.calls.find(([input]) => typeof input !== 'string' && input.url.endsWith('/artists/100/status'))
      expect(await (call![0] as Request).clone().json()).toEqual({ status: 'done' })
    })
  })

  it('marks every “To do” artist as Custom after asking', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Custom' }))
    expect(screen.getByText(/Mark all 1 "To do" artists as Custom\?/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Mark 1 as Custom' }))
    expect(await screen.findByText('Marked 1 artist as Custom.')).toBeInTheDocument()
    const call = fetchMock.mock.calls.find(([input]) => typeof input !== 'string' && input.url.endsWith('/artists/bulk-status'))
    expect(await (call![0] as Request).clone().json()).toEqual({ status: 'custom' })
  })

  it('tells people who don’t own the server', async () => {
    owner = false
    renderPage()
    expect(await screen.findByText(/Only the owner of Home can change artist images/)).toBeInTheDocument()
  })
})
