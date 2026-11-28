import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { VideoCover } from './VideoCover'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const motion = (reduced: boolean) =>
  vi.stubGlobal('matchMedia', () => ({ matches: reduced, addEventListener: () => undefined, removeEventListener: () => undefined }))

it('plays the video cover, muted and looping, only while the pointer is on it', () => {
  motion(false)
  render(
    <VideoCover video="/api/servers/1/library/albums/200/video">
      <img alt="OK Computer" />
    </VideoCover>,
  )
  expect(screen.getByLabelText('Has a video cover')).toBeInTheDocument()
  expect(screen.queryByTestId('video-cover')).not.toBeInTheDocument() // not on its own
  const cover = screen.getByAltText('OK Computer').parentElement!
  fireEvent.pointerEnter(cover)
  const video = screen.getByTestId('video-cover') as HTMLVideoElement
  expect(video.getAttribute('src')).toBe('/api/servers/1/library/albums/200/video')
  expect(video.muted && video.loop).toBe(true)
  fireEvent.pointerLeave(cover)
  expect(screen.queryByTestId('video-cover')).not.toBeInTheDocument()
})

it('stays still when the system asks for less motion, and does nothing without a video', () => {
  motion(true)
  const { rerender } = render(
    <VideoCover video="/v">
      <img alt="Cover" />
    </VideoCover>,
  )
  fireEvent.pointerEnter(screen.getByAltText('Cover').parentElement!)
  expect(screen.queryByTestId('video-cover')).not.toBeInTheDocument()
  rerender(
    <VideoCover video={null}>
      <img alt="Cover" />
    </VideoCover>,
  )
  expect(screen.queryByLabelText('Has a video cover')).not.toBeInTheDocument()
})
