import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GigsLayout, LibraryLayout, PlaylistRedirect } from './Sections'

let owner = true

beforeEach(() => {
  owner = true
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(
        JSON.stringify([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: owner, approved: true, connection_kind: 'local', music_section_keys: ['3'] }]),
        { headers: { 'content-type': 'application/json' } },
      ),
    ),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function Where() {
  return <p>at {useLocation().pathname}</p>
}

function renderAt(path: string) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/library" element={<LibraryLayout />}>
            <Route path="*" element={<Where />} />
            <Route index element={<Where />} />
          </Route>
          <Route path="/gigs" element={<GigsLayout />}>
            <Route index element={<Where />} />
            <Route path="been-to" element={<Where />} />
          </Route>
          <Route path="/playlists/:id" element={<PlaylistRedirect />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Library and Gigs', () => {
  it('has tabs for the library, playlists, rating and (owners) artwork and tag editing', async () => {
    renderAt('/library/playlists')
    expect(await screen.findByRole('link', { name: 'Artwork' })).toHaveAttribute('href', '/library/artwork')
    const tabs = screen.getByRole('navigation', { name: 'Library' })
    expect([...tabs.querySelectorAll('a')].map((a) => a.textContent)).toEqual(['Library', 'Stats', 'Playlists', 'Rate', 'Artwork', 'Tag editing'])
    expect(screen.getByRole('link', { name: 'Playlists' })).toHaveAttribute('aria-current', 'page')
  })

  it('leaves Artwork out for people who don’t own the server', async () => {
    owner = false
    renderAt('/library')
    expect(await screen.findByRole('link', { name: 'Rate' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Artwork' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Tag editing' })).not.toBeInTheDocument()
  })

  it('has Upcoming and Been to under Gigs', () => {
    renderAt('/gigs/been-to')
    expect(screen.getByRole('link', { name: 'Been to' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Upcoming' })).toHaveAttribute('href', '/gigs')
  })

  it('sends old playlist addresses to the new ones', () => {
    renderAt('/playlists/7')
    expect(screen.getByText('at /library/playlists/7')).toBeInTheDocument()
  })
})
