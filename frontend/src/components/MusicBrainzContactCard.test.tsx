import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { MusicBrainzContactCard } from './MusicBrainzContactCard'

let current: { contact: string | null; source: string | null }
let fetchMock: ReturnType<typeof vi.fn<(input: Request) => Promise<Response>>>

beforeEach(() => {
  current = { contact: 'env@example.org', source: 'environment' }
  fetchMock = vi.fn<(input: Request) => Promise<Response>>(async (input: Request) => {
    if (input.method === 'PUT') {
      const { contact } = await input.clone().json()
      current = contact ? { contact, source: 'settings' } : { contact: 'env@example.org', source: 'environment' }
    }
    return new Response(JSON.stringify(current), { headers: { 'content-type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('saves the contact MusicBrainz is told, over the one from the environment', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MusicBrainzContactCard />
    </QueryClientProvider>,
  )
  expect(await screen.findByText(/Using MUSICBRAINZ_CONTACT from the environment/)).toBeInTheDocument()
  const input = screen.getByRole('textbox', { name: 'Change it' })
  const save = screen.getByRole('button', { name: 'Save' })
  fireEvent.change(input, { target: { value: 'not a contact' } })
  expect(save).toBeDisabled()
  expect(screen.getByText(/An email address, or a web page/)).toBeInTheDocument()
  fireEvent.change(input, { target: { value: ' me@example.com ' } })
  fireEvent.click(save)
  expect(await screen.findByText('Saved. The worker uses it within a minute.')).toBeInTheDocument()
  expect(screen.getByText('me@example.com')).toBeInTheDocument()
  const put = fetchMock.mock.calls.find(([r]) => r.method === 'PUT')!
  expect(await put[0].clone().json()).toEqual({ contact: 'me@example.com' })
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
  expect(await screen.findByText(/Using MUSICBRAINZ_CONTACT from the environment/)).toBeInTheDocument()
})
