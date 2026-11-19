import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AlbumArtworkCard } from './FileArtwork'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const SHA = 'b019bbad1610d7421de3e67b00dce3c3560a4b98'

function setup(folderImages: unknown[]) {
  const fetch = vi.fn(async (input: Request) => {
    const url = new URL(input.url)
    if (url.pathname.endsWith('/albums/200/artwork'))
      return json({
        songs: 12, without_cover: 2, folder: 'Radiohead/OK Computer', plex_poster: true, folder_images: folderImages,
        covers: [{ sha1: SHA, mime: 'image/jpeg', bytes: 2_400_000, width: 3000, height: 3000, files: 10, oversized: true }],
      })
    if (input.method === 'POST') return json({ id: 9, status: 'queued' }, 201)
    return json({ detail: 'Not found' }, 404)
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AlbumArtworkCard serverId={1} albumKey={200} canWrite />
    </QueryClientProvider>,
  )
  return fetch
}

describe('Album artwork in the files', () => {
  it('embeds the folder cover in every song, player-sized', async () => {
    const fetch = setup([{ path: 'Radiohead/OK Computer/cover.jpg', kind: 'cover', sha1: 'c'.repeat(40), width: 1200, height: 1200, size: 300_000, error: null }])
    expect(await screen.findByText('10 of 12 songs · 3000×3000 · 2.4 MB')).toBeInTheDocument()
    expect(screen.getByText('Too big for some players')).toBeInTheDocument()
    expect(screen.getByText('2 without a cover.')).toBeInTheDocument()
    expect(screen.queryByText(/No cover.jpg yet/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Embed in 12 songs' }))
    await vi.waitFor(async () => {
      const post = fetch.mock.calls.find(([r]) => r.method === 'POST')
      expect(await post![0].clone().json()).toEqual({ source: 'folder', path: 'Radiohead/OK Computer/cover.jpg', max_px: 1000 })
    })
  })

  it("offers to save a cover.jpg when the folder has none", async () => {
    const fetch = setup([])
    expect(await screen.findByText('No cover.jpg or folder.jpg.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: "the songs' cover" }))
    await vi.waitFor(async () => {
      const post = fetch.mock.calls.find(([r]) => r.method === 'POST')
      expect(new URL(post![0].url).pathname).toBe('/api/servers/1/files/albums/200/artwork/folder')
      expect(await post![0].clone().json()).toEqual({ source: 'embedded', sha1: SHA, name: 'cover.jpg' })
    })
  })
})
