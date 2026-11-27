import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const health = vi.hoisted(() => ({ version: '0.1.240-staging' }))
const page = vi.hoisted(() => ({ version: '0.1.240-staging' as string | null }))

vi.mock('../api/hooks', () => ({ useHealth: () => ({ data: { version: health.version } }) }))
vi.mock('../version', async (original) => ({
  ...(await original<typeof import('../version')>()),
  get BUILD_VERSION() {
    return page.version
  },
}))

const { UpdateBanner } = await import('./UpdateBanner')

afterEach(cleanup)

describe('UpdateBanner', () => {
  it('says a staging build is running, without buttons once the page is up to date', () => {
    render(<UpdateBanner />)
    expect(screen.getByRole('status')).toHaveTextContent("You're running the staging version (0.1.240-staging)")
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers a reload when a newer build is running', () => {
    page.version = '0.1.239-staging'
    render(<UpdateBanner />)
    expect(screen.getByRole('status')).toHaveTextContent('Plekstender has been updated to 0.1.240-staging (a staging build).')
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()
  })

  it('shows nothing on a main build that is up to date', () => {
    page.version = '0.1.240'
    health.version = '0.1.240'
    render(<UpdateBanner />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
