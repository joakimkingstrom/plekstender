import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatChange, formatListeningTime } from '../format'
import { StatsPage } from './StatsPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const totals = { listens: 1200, listening_ms: 4_300 * 60_000, artists: 210, tracks: 900 }
const overview = {
  period: { key: '30d', start: '2026-10-20T00:00:00Z', end: '2026-11-18T12:00:00Z', previous_start: '2026-09-20T00:00:00Z' },
  totals,
  previous: { ...totals, listens: 1000 },
  new_artists: 14,
  top_artists: [
    { name: 'Radiohead', artist: null, listens: 120, listening_ms: 7_200_000, previous_rank: 3, rating_key: 100, thumb: '/library/metadata/100/thumb/1' },
    { name: 'Unknown Band', artist: null, listens: 40, listening_ms: 9_000_000, previous_rank: null, rating_key: null, thumb: null },
  ],
  top_albums: [],
  top_tracks: [{ name: 'Airbag', artist: 'Radiohead', listens: 12, listening_ms: 3_000_000, previous_rank: 1, rating_key: 200, thumb: null }],
  current_streak: 9,
  longest_streak: 41,
  first_listen_at: '2019-03-01T10:00:00Z',
}
const activity = {
  bucket: 'day',
  points: [
    { start: '2026-11-17', listens: 30 },
    { start: '2026-11-18', listens: 12 },
  ],
  heatmap: Array.from({ length: 7 }, (_, d) => Array.from({ length: 24 }, (_, h) => (d === 1 && h === 21 ? 34 : 0))),
}

function renderPage(extra: Record<string, unknown> = {}) {
  const fetch = vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname
    const body: Record<string, unknown> = {
      '/api/stats/overview': overview,
      '/api/stats/activity': activity,
      '/api/servers': [],
      '/api/listens': {
        total: 1,
        items: [{ id: 1, listened_at: '2026-11-17T21:10:00Z', source: 'plex', artist_name: 'Radiohead', album_name: 'OK Computer', track_name: 'Airbag', duplicate: false }],
      },
      ...extra,
    }
    return new Response(JSON.stringify(body[path] ?? { detail: 'Not found' }), {
      status: path in body ? 200 : 404,
      headers: { 'content-type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/history?range=30d']}>
        <StatsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return fetch
}

describe('StatsPage', () => {
  it('shows totals, top lists with rank changes, and offers every year since the first listen', async () => {
    const fetch = renderPage()
    expect(await screen.findByText('1,200')).toBeInTheDocument()
    expect(screen.getByText('+20% vs previous 30 days')).toBeInTheDocument()
    expect(screen.getByText('14 new to you')).toBeInTheDocument()
    expect(screen.getByText('9 days')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Radiohead' })).toHaveAttribute('href', '/library/artists/100')
    expect(screen.getByText('Unknown Band')).toBeInTheDocument() // not in the library: no link
    expect(screen.getByText('New')).toBeInTheDocument()
    const moved = screen.getByText('▲ 2')
    fireEvent.mouseEnter(moved)
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Previously #3')
    fireEvent.mouseLeave(moved)
    expect(screen.getByRole('button', { name: '2019' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'By time' }))
    expect(await screen.findByText('2 h 0 m')).toBeInTheDocument() // Radiohead's listening time
    await vi.waitFor(() =>
      expect(fetch.mock.calls.some(([r]) => new URL(r.url).searchParams.get('by') === 'time')).toBe(true),
    )

    fireEvent.click(screen.getByRole('button', { name: 'All time' }))
    await vi.waitFor(() =>
      expect(fetch.mock.calls.some(([r]) => new URL(r.url).searchParams.get('range') === 'all')).toBe(true),
    )
    const stats = fetch.mock.calls.find(([r]) => new URL(r.url).pathname === '/api/stats/overview')
    expect(new URL(stats![0].url).searchParams.get('tz')).toBeTruthy() // the viewer's time zone
  })

  it('has table views of the charts for screen readers', async () => {
    renderPage()
    expect(await screen.findByRole('table', { name: 'Listens by weekday and hour' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Listens per day' })).toBeInTheDocument()
    expect(screen.getByText('34')).toBeInTheDocument()
  })
})

describe('Most skipped', () => {
  it('lists songs and artists (not albums) with how many times each was skipped', async () => {
    renderPage({
      '/api/servers': [{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }],
      '/api/stats/skips': {
        threshold: 50,
        watching: true,
        items: [
          { rating_key: 300, name: 'Paranoid Android', artist: 'Radiohead', thumb: null, skips: 3, plays: 10 },
          { rating_key: 304, name: 'Glory Box', artist: 'Portishead', thumb: null, skips: 1, plays: 1 },
        ],
      },
    })
    const group = await screen.findByRole('group', { name: 'Skipped' })
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['Songs', 'Artists'])
    expect(await screen.findByText('3 skips')).toHaveAttribute('title', '3 of 10 plays skipped')
    expect(screen.getByText('1 skip')).toHaveAttribute('title', '1 of 1 play skipped')
  })
})

describe('chart details', () => {
  it('shows the listens behind a heatmap square', async () => {
    const fetch = renderPage()
    const day = new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(new Date(Date.UTC(2026, 10, 17, 12)))
    fireEvent.click(await screen.findByRole('button', { name: `${day} 21:00: 34 listens` }))
    const dialog = await screen.findByRole('dialog', { name: `${day} 21:00–22:00` })
    expect(await within(dialog).findByText('Airbag')).toBeInTheDocument()
    const request = fetch.mock.calls.map(([r]) => new URL(r.url)).find((u) => u.pathname === '/api/listens')!
    expect(Object.fromEntries(request.searchParams)).toMatchObject({ weekday: '2', hour: '21', counted: 'true' })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('stats formatting', () => {
  it('formats listening time and changes', () => {
    expect(formatListeningTime(90 * 60_000)).toBe('1 h 30 m')
    expect(formatListeningTime(45 * 60_000)).toBe('45 m')
    expect(formatListeningTime(125 * 3_600_000)).toBe('125 h')
    expect(formatChange(120, 100)).toBe('+20%')
    expect(formatChange(80, 100)).toBe('−20%')
    expect(formatChange(5, 0)).toBeNull()
  })
})
