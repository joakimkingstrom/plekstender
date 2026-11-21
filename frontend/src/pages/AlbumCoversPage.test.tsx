import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AlbumCovers } from './AlbumCoversPage'

const dummy = { rating_key: 202, title: 'Dummy', artist: 'Portishead', year: 1994, thumb: null, width: null, height: null, state: 'missing', kept: false, source: null, applied_at: null }
const oknotok = { rating_key: 201, title: 'OK Computer OKNOTOK', artist: 'Radiohead', year: 2017, thumb: '/library/metadata/201/thumb/1', width: 300, height: 300, state: 'low', kept: false, source: null, applied_at: null }
const counts = { missing: 1, low: 1, ok: 2, unchecked: 0, kept: 0 }

let jobs: unknown[] = []
let fetchMock: ReturnType<typeof vi.fn<(input: Request | string, init?: RequestInit) => Promise<Response>>>
const requests = (path: string) =>
  fetchMock.mock.calls.filter(([input]) => new URL(typeof input === 'string' ? input : input.url, 'http://localhost').pathname === path)

beforeEach(() => {
  jobs = []
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:cover', revokeObjectURL: () => undefined }))
  fetchMock = vi.fn(async (input: Request | string) => {
    const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    switch (url.pathname) {
      case '/api/servers/1/artwork/albums':
        return json({ items: url.searchParams.get('show') === 'low' ? [oknotok] : [dummy, oknotok], total: 2, counts })
      case '/api/servers/1/artwork/albums/201/candidates':
        return json({
          candidates: url.searchParams.get('source') === 'deezer'
            ? [{ token: 'tok-deezer', thumb: '/thumb/1', label: 'OK Computer OKNOTOK 1997 2017 · Radiohead', likes: null, width: 1000, height: 1000, up_to: null }]
            : [{ token: 'tok-itunes', thumb: '/thumb/2', label: 'OKNOTOK · Radiohead', likes: null, width: null, height: null, up_to: 3000 }],
          note: null,
        })  // prettier-ignore
      case '/api/servers/1/artwork/albums/201/preview':
        return new Response(new Blob(['jpeg']), { headers: { 'content-type': 'image/jpeg', 'x-image-width': '1000', 'x-image-height': '1000' } })
      case '/api/servers/1/artwork/albums/201/best':
        return json({ outcome: 'applied', cover: { ...oknotok, width: 1400, height: 1400, state: 'ok', source: 'itunes' }, source: 'itunes', width: 1400, height: 1400, problems: [] })
      case '/api/servers/1/artwork/albums/202/best':
        return json({ outcome: 'not_found', cover: null, source: null, width: null, height: null, problems: [] })
      case '/api/servers/1/artwork/albums/best':
        jobs = [{ id: 7, kind: 'covers', status: 'running', progress: { done: 1, total: 2 }, stats: {}, error: null, created_at: '2026-11-21T12:00:00Z', finished_at: null }]
        return json(jobs[0], 201)
      case '/api/servers/1/artwork/jobs':
        return json(jobs)
      case '/api/servers/1/artwork/albums/201/apply':
        return json({ ...oknotok, width: 1000, height: 1000, state: 'ok', source: 'deezer', applied_at: '2026-11-21T12:00:00Z' })
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

describe('Album covers', () => {
  it('lists the covers most in need, finds a better one and saves it to Plex', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <AlbumCovers serverId={1} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    const list = await screen.findByRole('list', { name: 'Albums' })
    expect(within(list).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'DDummyPortishead · 1994No cover', // (D: the letter shown where there's no image)
      'OK Computer OKNOTOKRadiohead · 2017300×300',
    ])
    expect(screen.getByRole('button', { name: /Needs a better one 2/ })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: /Low resolution 1/ }))
    await vi.waitFor(() => expect((requests('/api/servers/1/artwork/albums').at(-1)![0] as Request).url).toContain('show=low'))

    // Deezer first: a 1000 px cover; iTunes says how big it can be.
    fireEvent.click(await screen.findByRole('button', { name: /OK Computer OKNOTOK/ }))
    const found = await screen.findByRole('list', { name: 'Covers found' })
    expect(found).toHaveTextContent('1000×1000')
    fireEvent.click(screen.getByRole('tab', { name: 'iTunes' }))
    expect(await screen.findByText('up to 3000 px')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Deezer' }))
    fireEvent.click(await within(await screen.findByRole('list', { name: 'Covers found' })).findByRole('button'))
    expect(await screen.findByText('New · 1000×1000')).toBeInTheDocument()
    expect(screen.getByText('Now · 300×300')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Save to Plex' }))
    expect(await screen.findByText('Saved to Plex.')).toBeInTheDocument()
    const [request] = requests('/api/servers/1/artwork/albums/201/apply').at(-1)!
    expect(await (request as Request).clone().json()).toEqual({ source: { kind: 'candidate', token: 'tok-deezer' }, square: false })
  })

  it('applies the best match to one album, or to every album listed', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <AlbumCovers serverId={1} />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    // One album: found on iTunes at 1400 px and saved.
    fireEvent.click(await screen.findByRole('button', { name: /OK Computer OKNOTOK/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Apply best match' }))
    expect(await screen.findByText('Saved the best match to Plex (iTunes, 1400×1400).')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Dummy/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Apply best match' }))
    expect(await screen.findByText(/No match found: nothing on Deezer, iTunes, Tidal or the Cover Art Archive/)).toBeInTheDocument()

    // Every album listed, in the background.
    fireEvent.click(screen.getByRole('button', { name: 'Apply best matches' }))
    fireEvent.click(screen.getByRole('button', { name: 'Apply to 2' }))
    expect(await screen.findByText('Applying best matches…')).toBeInTheDocument()
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    const [started] = requests('/api/servers/1/artwork/albums/best')
    expect(await (started[0] as Request).clone().json()).toEqual({ show: 'needs' })
  })
})
