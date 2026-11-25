/** Editing music files' tags: fields, how values show, and working out the changes. */
import type { TagEdit } from './api/files'

export type TagField = TagEdit['field']
export type TagValue = string | string[] | null

export const FIELD_LABELS: Record<TagField, string> = {
  title: 'Title',
  artist: 'Artist',
  album: 'Album',
  albumartist: 'Album artist',
  date: 'Year',
  genre: 'Genre',
  tracknumber: 'Track',
  discnumber: 'Disc',
  composer: 'Composer',
  originaldate: 'Original date',
  releasecountry: 'Release country',
  label: 'Label',
  catalognumber: 'Catalogue number',
  barcode: 'Barcode',
  musicbrainz_albumid: 'MusicBrainz release ID',
  musicbrainz_releasegroupid: 'MusicBrainz release group ID',
  musicbrainz_albumartistid: 'MusicBrainz album artist ID',
}

/** Every tag Plekstender writes (MusicBrainz's too), for history and previews. */
const MORE_LABELS: Record<string, string> = {
  originaldate: 'Original date',
  releasecountry: 'Release country',
  musicbrainz_trackid: 'MusicBrainz recording ID',
  musicbrainz_releasetrackid: 'MusicBrainz track ID',
  musicbrainz_albumid: 'MusicBrainz release ID',
  musicbrainz_artistid: 'MusicBrainz artist ID',
  musicbrainz_albumartistid: 'MusicBrainz album artist ID',
  musicbrainz_releasegroupid: 'MusicBrainz release group ID',
  rating: 'Rating',
  path: 'File',
  picture: 'Cover',
  image: 'Folder picture',
  lrc: 'Lyrics (.lrc)',
  txt: 'Lyrics (.txt)',
  lyrics: 'Lyrics',
}

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field as TagField] ?? MORE_LABELS[field] ?? field
}

/** Fields set for the whole album at once, and per song. */
export const ALBUM_FIELDS: TagField[] = ['album', 'albumartist', 'date', 'genre']
export const SONG_FIELDS: TagField[] = ['tracknumber', 'title', 'artist']
/** Under "More fields": the release's details (Picard's tags) and, per song, disc and composer. */
export const MORE_ALBUM_FIELDS: TagField[] = [
  'originaldate',
  'releasecountry',
  'label',
  'catalognumber',
  'barcode',
  'musicbrainz_albumid',
  'musicbrainz_releasegroupid',
  'musicbrainz_albumartistid',
]
export const MORE_SONG_FIELDS: TagField[] = ['discnumber', 'composer']

export function showValue(value: TagValue | undefined): string {
  if (value === null || value === undefined || value === '') return '(none)'
  return Array.isArray(value) ? value.join(', ') : value
}

/** A value as shown for its field: ratings (0-100 in files) as stars, "4½ ★". */
export function showFieldValue(field: string, value: TagValue | undefined): string {
  if (field === 'rating' && typeof value === 'string' && /^\d+$/.test(value)) {
    const halves = Math.round(Number(value) / 10)
    return `${Math.floor(halves / 2) || ''}${halves % 2 ? '½' : ''} ★`
  }
  return showValue(value)
}

/** A change's old value: "not read yet" when the file was scanned before Plekstender read
 * that tag (writing then replaces whatever is there, and keeps it for undo). */
export function showOld(change: { field: string; old_value?: TagValue; old_unknown?: boolean }): string {
  return change.old_unknown ? 'not read yet' : showFieldValue(change.field, change.old_value)
}

/** MusicBrainz ID fields and the kind of page each ID is on musicbrainz.org. */
export const MB_PAGES: Record<string, string> = {
  musicbrainz_trackid: 'recording',
  musicbrainz_releasetrackid: 'track',
  musicbrainz_albumid: 'release',
  musicbrainz_artistid: 'artist',
  musicbrainz_albumartistid: 'artist',
  musicbrainz_releasegroupid: 'release-group',
}

/** A file's name without its folder and extension: "01 - Claws". */
export function songName(path: string): string {
  const name = path.split('/').at(-1) ?? path
  return name.replace(/\.[^.]+$/, '')
}

/** A typed-in value as it's stored: trimmed, genres split on commas, empty = no tag. */
export function parseValue(field: TagField, text: string): TagValue {
  if (field === 'genre') {
    const items = text
      .split(/[,;]/)
      .map((g) => g.trim().replace(/\s+/g, ' '))
      .filter(Boolean)
    return items.length ? items : null
  }
  const value = text.trim().replace(/\s+/g, ' ')
  return value || null
}

/** A file's value as text in an input. */
export function asText(value: unknown): string {
  if (value === null || value === undefined) return ''
  return Array.isArray(value) ? value.join(', ') : String(value)
}

/** The same value (genres in any order). */
export function sameValue(a: TagValue, b: TagValue): boolean {
  const norm = (v: TagValue) => (Array.isArray(v) ? [...v].sort() : v)
  return JSON.stringify(norm(a)) === JSON.stringify(norm(b))
}

export type EditableFile = { file_id: number; tags: Record<string, unknown>; path: string; title: string }
/** What's typed: album-wide fields (for every song), and per song. */
export type Draft = { album: Partial<Record<TagField, string>>; songs: Record<number, Partial<Record<TagField, string>>> }

export type PlannedChange = TagEdit & { path: string; songTitle: string; old: TagValue }

/** The changes a draft makes: every field whose new value differs from the file's. */
export function plannedChanges(files: EditableFile[], draft: Draft): PlannedChange[] {
  const out: PlannedChange[] = []
  for (const file of files) {
    for (const field of [...ALBUM_FIELDS, ...MORE_ALBUM_FIELDS, ...SONG_FIELDS, ...MORE_SONG_FIELDS]) {
      const typed = draft.album[field] ?? draft.songs[file.file_id]?.[field]
      if (typed === undefined) continue
      const old = parseValue(field, asText(file.tags[field]))
      const value = parseValue(field, typed)
      if (!sameValue(old, value))
        out.push({ file_id: file.file_id, field, value, old, path: file.path, songTitle: file.title })
    }
  }
  return out
}
