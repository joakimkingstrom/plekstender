import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { JobStop } from './JobStop'

let answer: Response
const asked: { url: string; body: unknown }[] = []
const fetchMock = vi.fn(async (input: Request) => {
  asked.push({ url: new URL(input.url).pathname, body: await input.json() })
  return answer
})

beforeEach(() => {
  asked.length = 0
  answer = new Response(JSON.stringify({ status: 'running', error: null, stop_requested: 'stop' }), {
    headers: { 'content-type': 'application/json' },
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const renderStop = (ui: React.ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>{ui}</QueryClientProvider>)

describe('Stopping a job', () => {
  it('stops at once, and says what Stop keeps; no Cancel when there is nothing to take back', async () => {
    renderStop(<JobStop kind="files" jobId={4} what="the scan" keeps="Stop keeps the files read so far." />)
    expect(screen.getByText('Stop keeps the files read so far.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Cancel/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stop the scan' }))
    expect(await screen.findByText('Stopping…')).toBeInTheDocument()
    expect(asked).toEqual([{ url: '/api/jobs/files/4/stop', body: { cancel: false } }])
  })

  it('cancels only once confirmed, and shows why it could not', async () => {
    answer = new Response(JSON.stringify({ detail: "It isn't running." }), {
      status: 409,
      headers: { 'content-type': 'application/json' },
    })
    renderStop(<JobStop kind="edits" jobId={9} what="the edit" cancel={<p>What it wrote is undone.</p>} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel the edit…' }))
    expect(screen.getByRole('dialog', { name: 'Cancel the edit?' })).toHaveTextContent('What it wrote is undone.')
    fireEvent.click(screen.getByRole('button', { name: 'Keep it running' }))
    expect(asked).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Cancel the edit…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel the edit' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("It isn't running.")
    expect(asked).toEqual([{ url: '/api/jobs/edits/9/stop', body: { cancel: true } }])
    expect(screen.getByRole('button', { name: 'Stop the edit' })).toBeInTheDocument() // can try again
  })

  it('shows a stop already asked for', () => {
    renderStop(<JobStop kind="imports" jobId={2} what="the import" requested="cancel" />)
    expect(screen.getByText('Cancelling…')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
