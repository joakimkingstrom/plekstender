import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PlexSync } from './PlexSync'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

describe('Writing Plex metadata to files', () => {
  it('previews the changes for the chosen tags, then writes them', async () => {
    const fetch = vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      if (url.pathname.endsWith('/files/plex-sync/preview'))
        return json({
          files: 2,
          changes: 3,
          by_field: { title: 2, albumartist: 1 },
          samples: [
            { path: 'A/B/01 x.flac', field: 'title', old_value: 'x', new_value: 'X' },
            { path: 'A/B/02 y.flac', field: 'title', old_value: 'y', new_value: 'Y' },
            { path: 'A/B/02 y.flac', field: 'albumartist', old_value: null, new_value: 'A' },
          ],
        })
      if (url.pathname.endsWith('/files/plex-sync')) return json({ id: 9, status: 'queued' }, 201)
      return json({ detail: 'Not found' }, 404)
    })
    vi.stubGlobal('fetch', fetch)
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PlexSync serverId={1} scope="artist" scopeKey={5} canWrite />
      </QueryClientProvider>,
    )
    fireEvent.click(screen.getByRole('checkbox', { name: 'Genre' }))
    fireEvent.click(screen.getByRole('button', { name: 'Check what would change' }))
    expect(await screen.findByText('3 changes')).toBeInTheDocument()
    expect(screen.getByText(/Title 2, Album artist 1/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Write 3 changes' }))
    await vi.waitFor(async () => {
      const post = fetch.mock.calls.find(([r]) => new URL(r.url).pathname.endsWith('/files/plex-sync'))
      const body = await post![0].clone().json()
      expect(body).toMatchObject({ scope: 'artist', key: 5 })
      expect(body.fields).not.toContain('genre')
      expect(body.fields).not.toContain('rating') // only when asked for
    })
    expect(await screen.findByText(/Writing in the background/)).toBeInTheDocument()
  })
})
