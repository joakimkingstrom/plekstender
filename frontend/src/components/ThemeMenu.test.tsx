import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useTheme } from '../theme'
import { CoverBackground } from './CoverBackground'
import { ThemeMenu } from './ThemeMenu'

let preferences: Record<string, unknown>
let reducedMotion = false
let systemLight = false

beforeEach(() => {
  preferences = {}
  reducedMotion = false
  systemLight = false
  localStorage.clear()
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('reduced-motion') ? reducedMotion : query.includes('light') ? systemLight : false,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      const me = () => ({ id: 1, username: 'me', email: null, thumb_url: null, is_admin: false, preferences })
      if (url.pathname === '/api/auth/me/preferences') {
        preferences = await input.clone().json()
        return json(me())
      }
      if (url.pathname === '/api/auth/me') return json(me())
      if (url.pathname === '/api/servers')
        return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      if (url.pathname === '/api/servers/1/library/stats/backgrounds')
        return json([
          { rating_key: 200, thumb: '/library/metadata/200/thumb/1' },
          { rating_key: 201, thumb: '/library/metadata/201/thumb/1' },
        ])
      return json([])
    }),
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const wrap = (ui: React.ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>
)

it('chooses light, dark or the system’s, and the covers behind the pages', async () => {
  const chosen: string[] = []
  render(wrap(<ThemeMenu theme="dark" choice="dark" onChoose={(c) => chosen.push(c)} />))
  fireEvent.click(screen.getByRole('button', { name: 'Theme and background' }))
  fireEvent.click(screen.getByRole('button', { name: 'System' }))
  expect(chosen).toEqual(['system'])
  fireEvent.click(screen.getByRole('button', { name: 'Colour' }))
  await vi.waitFor(() => expect(preferences).toMatchObject({ background: { mode: 'colour', family: 'blue', speed: 'normal' } }))
  fireEvent.click(await screen.findByRole('radio', { name: 'Red' }))
  await vi.waitFor(() => expect(preferences).toMatchObject({ background: { mode: 'colour', family: 'red' } }))
  fireEvent.click(screen.getByRole('button', { name: 'Slow' }))
  await vi.waitFor(() => expect(preferences).toMatchObject({ background: { speed: 'slow' } }))
  // Escape closes it.
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('shows covers behind the pages, unless the system asks for less motion', async () => {
  const settings = { mode: 'covers' as const, family: 'blue' as const, speed: 'normal' as const }
  const { unmount } = render(wrap(<CoverBackground theme="light" settings={settings} />))
  const layer = await screen.findByTestId('cover-background')
  const img = layer.querySelector('img')!
  expect(img.getAttribute('src')).toContain('size=1000')
  const asked = (vi.mocked(fetch).mock.calls as [Request][]).map(([r]) => new URL(r.url)).find((u) => u.pathname.endsWith('/backgrounds'))!
  expect(Object.fromEntries(asked.searchParams)).toMatchObject({ mode: 'covers', theme: 'light' })
  unmount()

  reducedMotion = true
  render(wrap(<CoverBackground theme="light" settings={settings} />))
  await new Promise((r) => setTimeout(r, 50))
  expect(screen.queryByTestId('cover-background')).not.toBeInTheDocument()
})

it('follows the system’s light or dark mode when chosen', () => {
  systemLight = true
  const { result } = renderHook(() => useTheme())
  expect(result.current[0]).toBe('dark')
  act(() => result.current[1]('system'))
  expect(result.current[0]).toBe('light')
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(localStorage.getItem('theme')).toBe('system')
})
