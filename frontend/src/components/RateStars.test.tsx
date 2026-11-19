import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RateStars } from './RateStars'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function setup(status: number) {
  const fetch = vi.fn(async (input: Request) => {
    const body = await input.clone().json()
    return new Response(
      JSON.stringify(status === 200 ? { rating_key: 300, item_type: 'track', user_rating: body.rating } : { detail: "Plex didn't save it" }),
      { status, headers: { 'content-type': 'application/json' } },
    )
  })
  vi.stubGlobal('fetch', fetch)
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RateStars serverId={1} ratingKey={300} value={null} label="Rate Airbag" />
    </QueryClientProvider>,
  )
  return fetch
}

describe('RateStars', () => {
  it('saves a rating to Plex from the library', async () => {
    const fetch = setup(200)
    const stars = screen.getByRole('slider', { name: 'Rate Airbag' })
    fireEvent.keyDown(stars, { key: '4' })
    expect(stars).toHaveAttribute('aria-valuenow', '4') // shown straight away
    await vi.waitFor(async () => expect(await fetch.mock.calls[0][0].clone().json()).toEqual({ rating_key: 300, rating: 8 }))
  })

  it('puts the old rating back and says why when Plex refuses', async () => {
    setup(502)
    const stars = screen.getByRole('slider', { name: 'Rate Airbag' })
    fireEvent.keyDown(stars, { key: '2' })
    expect(await screen.findByRole('alert')).toHaveTextContent("Plex didn't save it")
    expect(stars).toHaveAttribute('aria-valuenow', '0')
  })
})
