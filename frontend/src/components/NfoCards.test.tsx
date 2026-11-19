import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AlbumNfo, ArtistNfo } from './NfoCards'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
const RELEASE = '0b6b4ba0-d36f-47bd-b4ea-6a5b91842d29'
const wrap = (node: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>)

describe('.nfo cards', () => {
  it("shows an album.nfo and offers its release when the files don't have it", async () => {
    vi.stubGlobal('fetch', async () =>
      json({
        path: 'Radiohead/OK Computer/album.nfo', kind: 'album', error: null, file_release_ids: [], plex_mbid: null,
        data: { musicbrainz_albumid: RELEASE, release_date: '1997-05-21', label: 'Parlophone', genres: ['Rock'], moods: ['Bleak'], review: 'A landmark.' },
      }),
    )
    const onApply = vi.fn()
    wrap(<AlbumNfo serverId={1} albumKey={200} onApplyRelease={onApply} />)
    expect(await screen.findByText("Your files don't say which release they are.")).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '0b6b4ba0…' })).toHaveAttribute('href', `https://musicbrainz.org/release/${RELEASE}`)
    expect(screen.getByText('Parlophone')).toBeInTheDocument()
    expect(screen.getByText('Bleak')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Use this release…' }))
    expect(onApply).toHaveBeenCalledWith(RELEASE)
  })

  it("says when Plex has another artist ID than the artist.nfo", async () => {
    vi.stubGlobal('fetch', async () =>
      json({
        path: 'Radiohead/artist.nfo', kind: 'artist', error: null, file_release_ids: [], plex_mbid: 'ffffffff-0000-4000-8000-000000000000',
        data: { musicbrainz_artistid: 'a74b1b7f-71a5-4011-9441-d0b5e4122711', born: '1985', biography: 'An English rock band.' },
      }),
    )
    wrap(<ArtistNfo serverId={1} artistKey={100} />)
    expect(await screen.findByText('Plex has another one (ffffffff…).')).toBeInTheDocument()
    expect(screen.getByText('An English rock band.')).toBeInTheDocument()
    expect(screen.getByText('1985')).toBeInTheDocument()
  })
})
