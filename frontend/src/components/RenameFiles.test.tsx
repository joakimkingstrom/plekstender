import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RenameFiles } from './RenameFiles'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const job = (status: string) => ({ id: 4, status, progress: {}, stats: {}, error: null, created_at: '', finished_at: null })

describe('Renaming files', () => {
  it('checks where everything goes, then moves it', async () => {
    let polls = 0
    const fetch = vi.fn(async (input: Request) => {
      const url = new URL(input.url)
      if (url.pathname.endsWith('/files/renames') && input.method === 'POST') return json(job('queued'), 201)
      if (url.pathname.endsWith('/renames/4')) {
        polls += 1
        if (polls === 1) return json({ job: job('running'), template: null, songs: 0, unchanged: 0, moves: [], skipped: [], applied_set_id: null })
        return json({
          job: job('succeeded'),
          template: '{albumartist}/{album}/{title}',
          songs: 3,
          unchanged: 1,
          moves: [
            { kind: 'song', source: 'in/01.flac', target: 'Radiohead/OK Computer/Airbag.flac' },
            { kind: 'folder', source: 'in/cover.jpg', target: 'Radiohead/OK Computer/cover.jpg' },
          ],
          skipped: [{ path: 'in/02.flac', target: 'Radiohead/OK Computer/Airbag.flac', reason: 'Another song would get this name.' }],
          applied_set_id: null,
        })
      }
      if (url.pathname.endsWith('/renames/4/apply')) return json({ id: 9, status: 'queued' }, 201)
      return json({ detail: 'Not found' }, 404)
    })
    vi.stubGlobal('fetch', fetch)
    const onClose = vi.fn()
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RenameFiles serverId={1} scope="album" scopeKey={200} canWrite onClose={onClose} />
      </QueryClientProvider>,
    )
    const field = screen.getByRole('textbox', { name: 'Template' })
    expect(field).toHaveValue('{albumartist}/{album}/[{disc}-][{track:02} - ]{title}')
    fireEvent.change(field, { target: { value: '{albumartist}/{album}/{title}' } })
    fireEvent.click(screen.getByRole('button', { name: 'Check where everything goes' }))

    expect(await screen.findByText('1 song moves', {}, { timeout: 3000 })).toBeInTheDocument()
    const body = await fetch.mock.calls.find(([r]) => r.method === 'POST')![0].clone().json()
    expect(body).toEqual({ scope: 'album', key: 200, template: '{albumartist}/{album}/{title}' })
    expect(screen.getByText(/1 other file goes along/)).toBeInTheDocument()
    expect(screen.getByText('Radiohead/OK Computer/')).toBeInTheDocument()
    expect(screen.getByText('from in/01.flac')).toBeInTheDocument()
    expect(screen.getByText(/Another song would get this name/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Move 2 files' }))
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(fetch.mock.calls.some(([r]) => r.url.endsWith('/renames/4/apply'))).toBe(true)
  })
})
