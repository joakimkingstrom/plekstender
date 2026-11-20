import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { AddFonts, OfferedFonts } from './SettingsPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Text logo fonts', () => {
  it('previews the offered fonts, then browses Google Fonts and adds one (any capitals)', async () => {
    let finishSaving = () => {}
    const saved = new Promise<void>((resolve) => (finishSaving = resolve))
    let offered = [
      { name: 'Sans serif', fonts: ['Anton'] },
      { name: 'Serif', fonts: [] },
      { name: 'Display', fonts: ['Monoton'] },
      { name: 'Handwriting', fonts: [] },
      { name: 'Monospace', fonts: [] },
    ]
    const fetch = vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/artwork/fonts') return json(offered)
      if (url.pathname === '/api/admin/fonts/catalogue') {
        const q = url.searchParams.get('q') ?? ''
        const all = [
          { name: 'Roboto', group: 'Sans serif', offered: offered[0].fonts.includes('Roboto') },
          { name: 'Roboto Slab', group: 'Serif', offered: false },
          { name: 'Anton', group: 'Sans serif', offered: true },
        ]
        const items = all.filter((f) => f.name.toLowerCase().includes(q.toLowerCase()))
        return json({ items, total: items.length, available: true })
      }
      if (url.pathname === '/api/admin/fonts' && input.method === 'POST') {
        await saved // the server takes a moment
        offered = offered.map((g) => (g.name === 'Sans serif' ? { ...g, fonts: ['Anton', 'Roboto'] } : g))
        return json(offered)
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetch)
    const show = (page: ReactNode) =>
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter>{page}</MemoryRouter>
        </QueryClientProvider>,
      )
    show(<OfferedFonts />)
    // Each offered font is shown in itself.
    expect(await screen.findByAltText('Anton in Anton')).toHaveAttribute('src', '/api/artwork/fonts/preview.png?family=Anton')
    // The preview text applies to every font.
    fireEvent.change(screen.getByPlaceholderText(/Each font's name/), { target: { value: 'Radiohead' } })
    expect(await screen.findByAltText('Radiohead in Monoton')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Google Fonts' })).toHaveAttribute('href', '/settings/fonts/add')

    // The other page: adding from Google Fonts.
    cleanup()
    show(<AddFonts />)
    fireEvent.change(screen.getByPlaceholderText(/Each font's name/), { target: { value: 'Radiohead' } })
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search Google Fonts' }), { target: { value: 'roboto' } })
    expect(await screen.findByText('Roboto Slab · Serif')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add Roboto' }))
    // While it's saved: the button says so (and other Add buttons wait).
    expect(await screen.findByText('Adding…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add Roboto Slab' })).toBeDisabled()
    const catalogueCalls = () => fetch.mock.calls.filter(([r]) => new URL(r.url).pathname === '/api/admin/fonts/catalogue').length
    const before = catalogueCalls()
    finishSaving()
    // Then: said, marked in the results, and in the list without a reload.
    expect(await screen.findByText(/Roboto added to Sans serif/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add Roboto' })).not.toBeInTheDocument()
    await vi.waitFor(() => expect(catalogueCalls()).toBeGreaterThan(before))
    await vi.waitFor(async () => {
      const post = fetch.mock.calls.find(([r]) => r.method === 'POST')
      expect(await post![0].clone().json()).toEqual({ name: 'Roboto' })
    })
    // Still in the search results, now as offered.
    expect(screen.getByAltText('Radiohead in Roboto')).toBeInTheDocument()
  })
})
