import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Artwork, Stars } from './library'

describe('Stars', () => {
  it('shows Plex 0-10 ratings as stars out of 5', () => {
    render(<Stars rating={7} />)
    expect(screen.getByRole('img', { name: '3.5 out of 5 stars' })).toBeInTheDocument()
  })

  it('says when something is unrated', () => {
    render(<Stars rating={null} />)
    expect(screen.getByText('Unrated')).toBeInTheDocument()
  })
})

describe('Artwork', () => {
  it('loads Plex artwork through the image proxy', () => {
    const { container } = render(<Artwork serverId={4} path="/library/metadata/1/thumb/2" title="Radiohead" size={400} />)
    const img = container.querySelector('img')
    expect(img?.getAttribute('src')).toBe('/api/servers/4/image?path=%2Flibrary%2Fmetadata%2F1%2Fthumb%2F2&size=400')
  })

  it('falls back to a letter without artwork', () => {
    render(<Artwork serverId={4} path={null} title="radiohead" />)
    expect(screen.getByText('R')).toBeInTheDocument()
  })
})
