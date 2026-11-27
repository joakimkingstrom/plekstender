import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LidarrCard } from './LidarrCard'

let sent: { method: string; path: string; body: unknown }[] = []
const OPTIONS = {
  version: '2.9.6',
  root_folders: [{ path: '/music', free_space: 4_000_000_000_000 }],
  quality_profiles: [
    { id: 1, name: 'Any' },
    { id: 2, name: 'Lossless' },
  ],
  metadata_profiles: [{ id: 1, name: 'Standard' }],
}

beforeEach(() => {
  sent = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const path = new URL(input.url).pathname
      const body = input.method === 'GET' ? null : await input.clone().json()
      sent.push({ method: input.method, path, body })
      const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'content-type': 'application/json' } })
      if (path === '/api/admin/lidarr/check')
        return (body as { api_key: string | null }).api_key === 'right-key' ? json(OPTIONS) : json({ detail: "Lidarr didn't accept the API key." }, 502)
      if (path === '/api/admin/lidarr' && input.method === 'PUT') return json({ configured: true, ...(body as object), api_key: undefined })
      return json({ configured: false, url: null, root_folder: null, quality_profile_id: null, metadata_profile_id: null, access: 'admins' })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('checks the address and key, then saves the choices', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <LidarrCard />
    </QueryClientProvider>,
  )
  expect(await screen.findByText('Not connected.')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Address'), { target: { value: 'http://lidarr:8686' } })
  fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'wrong' } })
  fireEvent.click(screen.getByRole('button', { name: 'Check' }))
  expect(await screen.findByText("Lidarr didn't accept the API key.")).toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'right-key' } })
  fireEvent.click(screen.getByRole('button', { name: 'Check' }))
  expect(await screen.findByText('/music (4 TB free)')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Everyone' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByText('Saved.')).toBeInTheDocument()
  expect(sent.find((r) => r.method === 'PUT')?.body).toEqual({
    url: 'http://lidarr:8686',
    api_key: 'right-key',
    root_folder: '/music',
    quality_profile_id: 1,
    metadata_profile_id: 1,
    access: 'everyone',
  })
})
