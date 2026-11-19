import { describe, expect, it } from 'vitest'
import { parseValue, plannedChanges, sameValue, showValue, showFieldValue } from './tags'

const file = (id: number, tags: Record<string, unknown>) => ({ file_id: id, tags, path: `a/${id}.flac`, title: `Song ${id}` })

describe('tag edits', () => {
  it('parses typed values as they are stored', () => {
    expect(parseValue('genre', ' Rock,  Alternative ;Art Rock ,')).toEqual(['Rock', 'Alternative', 'Art Rock'])
    expect(parseValue('title', '  Karma   Police ')).toBe('Karma Police')
    expect(parseValue('artist', '   ')).toBeNull()
    expect(sameValue(['Rock', 'Alternative'], ['Alternative', 'Rock'])).toBe(true)
    expect(showValue(null)).toBe('(none)')
  })

  it('plans only what changes: album-wide fields for every song, and per song', () => {
    const files = [
      file(1, { title: 'Airbag', albumartist: 'Radiohead', genre: ['Rock'] }),
      file(2, { title: 'Paranoid Android', albumartist: 'radiohead', genre: ['Rock'] }),
    ]
    const changes = plannedChanges(files, {
      album: { albumartist: 'Radiohead', genre: 'Rock' },
      songs: { 1: { title: 'Airbag' }, 2: { title: 'Paranoid Android (Remastered)' } },
    })
    expect(changes.map(({ file_id, field, value, old }) => ({ file_id, field, value, old }))).toEqual([
      { file_id: 2, field: 'albumartist', value: 'Radiohead', old: 'radiohead' },
      { file_id: 2, field: 'title', value: 'Paranoid Android (Remastered)', old: 'Paranoid Android' },
    ])
  })
})

describe('showFieldValue', () => {
  it('shows ratings as stars', () => {
    expect(showFieldValue('rating', '100')).toBe('5 ★')
    expect(showFieldValue('rating', '90')).toBe('4½ ★')
    expect(showFieldValue('rating', '10')).toBe('½ ★')
    expect(showFieldValue('rating', null)).toBe('(none)')
    expect(showFieldValue('title', 'Airbag')).toBe('Airbag')
  })
})
