import { useId, useState } from 'react'
import { Link } from 'react-router'
import type { Schemas } from '../api/client'
import { formatCount } from '../format'
import { Artwork } from './library'
import { Tooltip } from './Tooltip'
import { Card } from './ui'

/** A live album's or a compilation's songs by the release each first came out on: one bar
 * per release, oldest first. Clicking a release lists its songs (as this album has them); the
 * arrow beside it goes to the release. A release by another artist than the album's (a
 * various artists compilation) is named artist first. */
export function SongSources({
  serverId,
  sources,
  unmatched,
  albumArtist,
  compilation,
}: {
  serverId: number
  sources: Schemas['SongSource'][]
  unmatched: number
  albumArtist: string | null
  compilation: boolean
}) {
  const id = useId()
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set())
  if (sources.length === 0) return null
  const max = Math.max(...sources.map((s) => s.songs))
  const total = sources.reduce((n, s) => n + s.songs, 0) + unmatched
  const discs = sources.some((s) => s.tracks.some((t) => (t.disc ?? 1) > 1))
  const toggle = (key: number) =>
    setOpen((was) => {
      const next = new Set(was)
      if (!next.delete(key)) next.add(key)
      return next
    })
  return (
    <Card title="Where the songs come from">
      <ol className="space-y-1.5" aria-label="Songs by release">
        {sources.map((s) => {
          const name = s.artist && s.artist !== albumArtist ? `${s.artist} · ${s.title}` : s.title
          const label = `${formatCount(s.songs)} ${s.songs === 1 ? 'song' : 'songs'} from ${name}${s.year ? ` (${s.year})` : ''}`
          const shown = open.has(s.rating_key)
          const songs = `${id}-${s.rating_key}`
          return (
            <li key={s.rating_key}>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  aria-label={label}
                  aria-expanded={shown}
                  aria-controls={songs}
                  onClick={() => toggle(s.rating_key)}
                  className="flex min-w-0 flex-1 items-center gap-2 rounded-lg p-1 text-left hover:bg-surface-2/60"
                >
                  <Artwork serverId={serverId} path={s.thumb} title={s.title} size={96} className="size-8 shrink-0 rounded" />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-2 text-sm">
                      <span className="truncate">
                        {name}
                        <span className="text-xs text-muted">
                          {[s.year, s.release_type !== 'Album' ? s.release_type : null].filter(Boolean).map((p) => ` · ${p}`).join('')}
                        </span>
                      </span>
                      <span className="shrink-0 text-xs text-muted tabular-nums">{formatCount(s.songs)}</span>
                    </span>
                    <span className="mt-1 block h-1.5 rounded-full bg-surface-2">
                      <span className="block h-full rounded-full bg-accent" style={{ width: `${(100 * s.songs) / max}%` }} />
                    </span>
                  </span>
                </button>
                <Tooltip content={`Go to ${name}`}>
                  <Link
                    to={`/library/albums/${s.rating_key}`}
                    aria-label={`Go to ${name}`}
                    className="shrink-0 rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text"
                  >
                    <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M5 12h14" />
                      <path d="M13 6l6 6-6 6" />
                    </svg>
                  </Link>
                </Tooltip>
              </div>
              {shown && (
                <ol id={songs} aria-label={`Songs from ${name}`} className="mb-2 mt-1 space-y-0.5 px-1 text-sm">
                  {s.tracks.map((t) => (
                    <li key={t.rating_key} className="flex gap-2">
                      <span className="min-w-8 shrink-0 whitespace-nowrap text-right text-xs leading-5 text-muted tabular-nums">
                        {t.number != null ? (discs ? `${t.disc ?? 1}-${t.number}` : t.number) : ''}
                      </span>
                      <span className="min-w-0 truncate">{t.title}</span>
                    </li>
                  ))}
                </ol>
              )}
            </li>
          )
        })}
      </ol>
      {unmatched > 0 && (
        <p className="mt-3 text-xs text-muted">
          {formatCount(unmatched)} of {formatCount(total)} songs{' '}
          {compilation
            ? 'aren’t on another album, EP, or single in the library (only on compilations, or on releases you don’t have).'
            : 'aren’t on one of the artist’s studio releases in the library (covers, new songs, or releases you don’t have).'}
        </p>
      )}
    </Card>
  )
}
