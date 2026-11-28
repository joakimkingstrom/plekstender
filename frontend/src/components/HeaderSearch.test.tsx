import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { HeaderSearch } from './HeaderSearch'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
      if (url.pathname === '/api/servers')
        return json([{ id: 1, machine_identifier: 'm', name: 'Home', is_owner: true, approved: true, connection_kind: 'local', music_section_keys: ['3'] }])
      if (url.pathname === '/api/servers/1/library/search')
        return json({
          artists: [{ rating_key: 100, title: 'Radiohead', thumb: null, subtitle: null, album_rating_key: null }],
          albums: [{ rating_key: 200, title: 'OK Computer', thumb: null, subtitle: 'Radiohead', album_rating_key: null }],
          songs: [{ rating_key: 301, title: 'Karma Police', thumb: null, subtitle: 'Radiohead · OK Computer', album_rating_key: 200 }],
        })
      return json([])
    }),
  )
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>
}

it('finds artists, albums and songs, moves with the arrow keys and opens with Enter', async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/']}>
        <HeaderSearch />
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const [field] = screen.getAllByRole('combobox', { name: 'Search the library' })
  fireEvent.change(field, { target: { value: 'ra' } })
  const list = await screen.findByRole('listbox', { name: 'Search results' })
  expect(await within(list).findByRole('group', { name: 'Songs' })).toBeInTheDocument()
  const options = within(list).getAllByRole('option')
  expect(options).toHaveLength(3)
  expect(options[0]).toHaveTextContent('Radiohead')
  expect(options[1]).toHaveTextContent('OK ComputerRadiohead')
  expect(options[2]).toHaveTextContent('Karma PoliceRadiohead · OK Computer')
  // The first is picked to start with; down twice: the song, which opens its album.
  expect(within(list).getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true')
  fireEvent.keyDown(field, { key: 'ArrowDown' })
  fireEvent.keyDown(field, { key: 'ArrowDown' })
  fireEvent.keyDown(field, { key: 'Enter' })
  expect(screen.getByTestId('where')).toHaveTextContent('/library/albums/200')
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()

  // Enter right away: the first result.
  fireEvent.change(field, { target: { value: 'radio' } })
  await screen.findByRole('listbox', { name: 'Search results' })
  await screen.findAllByRole('option')
  fireEvent.keyDown(field, { key: 'Enter' })
  expect(screen.getByTestId('where')).toHaveTextContent('/library/artists/100')
})
