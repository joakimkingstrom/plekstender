import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsLayout } from './SettingsPage'

let admin = true
let owner = true

beforeEach(() => {
  admin = true
  owner = true
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === 'string' ? input : input.url, 'http://localhost')
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/auth/me') return json({ id: 1, username: 'me', email: null, thumb_url: null, is_admin: admin })
      return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: owner, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderAt(path: string) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/settings" element={<SettingsLayout />}>
            <Route index element={<p>servers</p>} />
            <Route path="*" element={<p>a section</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Settings', () => {
  it('groups the sections: yours, the library’s (owners) and the admin’s', async () => {
    renderAt('/settings/services/gigs')
    const nav = screen.getByRole('navigation', { name: 'Settings' })
    expect(await within(nav).findByRole('link', { name: 'Services' })).toHaveAttribute('aria-current', 'page')
    // Services opens its kinds underneath; the one shown is marked.
    expect(within(nav).getByRole('link', { name: 'Gigs' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Identifying files' })).toHaveAttribute('href', '/settings/services/files')
    // Text logo fonts' parts only open when it's the section shown.
    expect(within(nav).queryByRole('link', { name: 'Add from Google Fonts' })).not.toBeInTheDocument()
    expect([...nav.querySelectorAll('p')].map((p) => p.textContent)).toEqual(['You', 'Library', 'Admin'])
    expect(within(nav).getByRole('link', { name: 'Scrobbling' })).toHaveAttribute('href', '/settings/scrobbling')
    expect(screen.getByRole('heading', { name: /^Services\s*·\s*Gigs$/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Settings section/ })).toBeInTheDocument() // the phone's picker
  })

  it('splits Export & import into parts, with artist logos for owners only', async () => {
    renderAt('/settings/export/gigs')
    const nav = screen.getByRole('navigation', { name: 'Settings' })
    expect(await within(nav).findByRole('link', { name: 'Export & import' })).toHaveAttribute('href', '/settings/export/history')
    expect(within(nav).getByRole('link', { name: 'Gigs' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Library' })).toHaveAttribute('href', '/settings/export/library')
    expect(await within(nav).findByRole('link', { name: 'Artist logos' })).toHaveAttribute('href', '/settings/export/logos')
    expect(screen.getByRole('heading', { name: /^Export & import\s*·\s*Gigs$/ })).toBeInTheDocument()
    cleanup()
    owner = false
    renderAt('/settings/export/history')
    const again = screen.getByRole('navigation', { name: 'Settings' })
    expect(await within(again).findByRole('link', { name: 'History' })).toHaveAttribute('aria-current', 'page')
    await new Promise((r) => setTimeout(r, 20))
    expect(within(again).queryByRole('link', { name: 'Artist logos' })).not.toBeInTheDocument()
  })

  it('shows only your own sections to someone who isn’t an admin or owner', async () => {
    admin = false
    owner = false
    renderAt('/settings')
    const nav = screen.getByRole('navigation', { name: 'Settings' })
    expect(await within(nav).findByRole('link', { name: 'Servers' })).toHaveAttribute('aria-current', 'page')
    await new Promise((r) => setTimeout(r, 20))
    expect([...nav.querySelectorAll('p')].map((p) => p.textContent)).toEqual(['You'])
    expect(within(nav).queryByRole('link', { name: 'Services' })).not.toBeInTheDocument()
  })
})
