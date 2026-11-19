import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { platform } from '../install'
import { InstallPrompt } from './InstallPrompt'

const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1'
const ANDROID = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36'

function device(ua: string, { phone = true, standalone = false } = {}) {
  vi.stubGlobal('navigator', { ...navigator, userAgent: ua, maxTouchPoints: 5, standalone })
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('max-width') ? phone : query.includes('standalone') ? standalone : false,
    media: query,
    addEventListener() {},
    removeEventListener() {},
  }))
}

beforeEach(() => localStorage.clear())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('InstallPrompt', () => {
  it('shows the iPhone steps once', () => {
    device(IPHONE)
    render(<InstallPrompt />)
    expect(screen.getByRole('dialog', { name: 'Put Plekstender on your home screen' })).toBeInTheDocument()
    expect(screen.getByText(/In Safari, tap the/)).toBeInTheDocument()
    expect(screen.getByText('Add to Home Screen')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    cleanup()
    render(<InstallPrompt />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument() // not again
  })

  it('shows the Android steps', () => {
    device(ANDROID)
    render(<InstallPrompt />)
    expect(screen.getByText(/In Chrome, tap the menu/)).toBeInTheDocument()
    expect(screen.queryByText(/In Safari/)).not.toBeInTheDocument()
  })

  it('stays away in the installed app and on computers', () => {
    device(IPHONE, { standalone: true })
    render(<InstallPrompt />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    cleanup()
    device(IPHONE, { phone: false })
    render(<InstallPrompt />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('tells iPads (which say they are Macs) from Macs', () => {
    const ipad = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15'
    expect(platform(ipad, 5)).toBe('ios')
    expect(platform(ipad, 0)).toBe('other')
    expect(platform(ANDROID, 5)).toBe('android')
  })
})
