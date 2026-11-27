import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { AlbumLyricsIcon, SongLyricsIcon } from './LyricsIcon'

afterEach(cleanup)

it('says what lyrics a song has, and nothing when its files are not read', () => {
  const { container, rerender } = render(<SongLyricsIcon kind="word" />)
  expect(screen.getByText('Word-synced lyrics')).toBeInTheDocument()
  rerender(<SongLyricsIcon kind="synced" />)
  expect(screen.getByText('Synced lyrics')).toBeInTheDocument()
  rerender(<SongLyricsIcon kind="none" />)
  expect(screen.getByText('No lyrics')).toBeInTheDocument()
  rerender(<SongLyricsIcon kind={null} />)
  expect(container).toBeEmptyDOMElement()
})

it('says how many of an album’s songs have lyrics', () => {
  const { container, rerender } = render(<AlbumLyricsIcon lyrics={{ songs: 10, with_lyrics: 10 }} />)
  expect(screen.getByText('Lyrics for all 10 songs')).toBeInTheDocument()
  rerender(<AlbumLyricsIcon lyrics={{ songs: 10, with_lyrics: 9 }} />)
  expect(screen.getByText('Lyrics for 9 of 10 songs')).toBeInTheDocument()
  // Some: never drawn as complete.
  expect(container.querySelectorAll('g[opacity="1"]')).toHaveLength(2)
  rerender(<AlbumLyricsIcon lyrics={{ songs: 10, with_lyrics: 0 }} />)
  expect(screen.getByText('No lyrics in its songs’ files')).toBeInTheDocument()
  rerender(<AlbumLyricsIcon lyrics={null} />)
  expect(container).toBeEmptyDOMElement()
})
