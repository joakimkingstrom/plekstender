import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CoverSourcesCard } from './CoverSourcesCard'

let saved: unknown[] = []

beforeEach(() => {
  saved = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (input.method === 'PUT') {
        const body = await input.clone().json()
        saved.push(body)
        return json(body)
      }
      return json({ deezer: true, itunes: true, coverartarchive: true })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('turns an album cover source off', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CoverSourcesCard />
    </QueryClientProvider>,
  )
  const deezer = await screen.findByRole('checkbox', { name: /Deezer/ })
  expect(deezer).toBeChecked()
  fireEvent.click(deezer)
  await vi.waitFor(() => expect(saved).toEqual([{ deezer: false, itunes: true, coverartarchive: true }]))
  expect(await screen.findByRole('checkbox', { name: /Deezer/ })).not.toBeChecked()
})
