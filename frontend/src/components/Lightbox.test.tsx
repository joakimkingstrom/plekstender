import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { Zoomable } from './Lightbox'

afterEach(cleanup)

describe('Zoomable', () => {
  it('opens a larger image and closes with Esc or a click outside', () => {
    render(
      <Zoomable image={{ src: '/big.jpg', alt: 'OK Computer', caption: 'Radiohead' }}>
        <img src="/small.jpg" alt="" />
      </Zoomable>,
    )
    const opener = screen.getByRole('button', { name: 'Show OK Computer larger' })
    opener.focus() // as a real click would
    fireEvent.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'OK Computer' })
    expect(screen.getByAltText('OK Computer')).toHaveAttribute('src', '/big.jpg')
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()

    fireEvent.click(opener)
    fireEvent.click(screen.getByRole('dialog'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(dialog).not.toBeInTheDocument()
  })
})
