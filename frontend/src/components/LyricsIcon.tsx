import type { Schemas } from '../api/client'
import { formatCount } from '../format'
import { Tooltip } from './Tooltip'

type Kind = NonNullable<Schemas['TrackOut']['lyrics']>

const SONG: Record<Kind, string> = {
  word: 'Word-synced lyrics',
  synced: 'Synced lyrics',
  plain: 'Plain lyrics (no times)',
  instrumental: 'Instrumental',
  none: 'No lyrics',
}

/** Three lines of text, drawn to show the kind: plain lines, a time before each line
 * (synced), lines split into timed words (word-synced); a note for an instrumental. */
function Glyph({ kind, lit = 3 }: { kind: Kind; lit?: number }) {
  if (kind === 'instrumental') {
    return (
      <svg viewBox="0 0 16 16" className="size-3.5" aria-hidden fill="currentColor">
        <path d="M6 3.2 13 2v8.2a2 2 0 1 1-1.2-1.8V4.6L7.2 5.4v6.8a2 2 0 1 1-1.2-1.8z" />
      </svg>
    )
  }
  const timed = kind === 'synced' || kind === 'word'
  const ys = [4, 8, 12]
  return (
    <svg viewBox="0 0 16 16" className="size-3.5" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      {ys.map((y, i) => (
        <g key={y} opacity={i < lit ? 1 : 0.3}>
          {timed && <circle cx="2.2" cy={y} r="0.9" fill="currentColor" stroke="none" />}
          {kind === 'word' ? (
            <>
              <path d={`M5 ${y}h3.2`} />
              <path d={`M10.2 ${y}H14`} />
            </>
          ) : (
            <path d={`M${timed ? 5 : 2} ${y}H14`} />
          )}
        </g>
      ))}
      {kind === 'none' && <path d="M2.5 13.5 13.5 2.5" />}
    </svg>
  )
}

/** A song's lyrics, as its files have them. */
export function SongLyricsIcon({ kind }: { kind: Schemas['TrackOut']['lyrics'] | undefined }) {
  if (!kind) return null
  const label = SONG[kind]
  return (
    <Tooltip content={label} className="shrink-0">
      <span
        className={kind === 'none' ? 'text-muted opacity-50' : ''}
        style={kind === 'none' ? undefined : { color: `var(--color-lyrics-${kind})` }}
      >
        <Glyph kind={kind} />
        <span className="sr-only">{label}</span>
      </span>
    </Tooltip>
  )
}

/** How many of an album's songs have lyrics in their files: all, some or none. */
export function AlbumLyricsIcon({ lyrics }: { lyrics: Schemas['AlbumSummary']['lyrics'] | undefined }) {
  if (!lyrics || lyrics.songs === 0) return null
  const { songs, with_lyrics: some } = lyrics
  const all = some >= songs
  const label =
    some === 0
      ? 'No lyrics in its songs’ files'
      : all
        ? `Lyrics for all ${formatCount(songs)} songs`
        : `Lyrics for ${formatCount(some)} of ${formatCount(songs)} songs`
  return (
    <Tooltip content={label} className="shrink-0">
      <span
        className={some === 0 ? 'text-muted opacity-50' : ''}
        style={some === 0 ? undefined : { color: 'var(--color-lyrics-synced)' }}
      >
        <Glyph kind={some === 0 ? 'none' : 'plain'} lit={some === 0 || all ? 3 : Math.min(2, Math.max(1, Math.round((3 * some) / songs)))} />
        <span className="sr-only">{label}</span>
      </span>
    </Tooltip>
  )
}
