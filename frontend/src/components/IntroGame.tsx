import { useState } from 'react'
import { useGameSearch, type GameMode, type GameSearchItem } from '../api/game'
import { PENALTY, ROUND } from '../game'
import { Artwork } from './library'

/** The 30-second bar over the card. */
export function RoundClock({ elapsed, loading }: { elapsed: number; loading: boolean }) {
  const left = Math.max(0, ROUND - elapsed)
  return (
    <div className="space-y-1" aria-live="off">
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div
          className={`h-full rounded-full ${left <= 5 ? 'bg-danger' : 'bg-accent'} ${loading ? 'w-1/4 animate-pulse' : ''}`}
          style={loading ? undefined : { width: `${(left / ROUND) * 100}%` }}
        />
      </div>
      <p className="text-right text-xs tabular-nums text-muted">{loading ? 'Getting the song…' : `${Math.ceil(left)} s`}</p>
    </div>
  )
}

/**
 * The guess field. Artists (and albums, in album mode) are suggested from 3 letters. For
 * songs: pick an artist first and then search their songs, or search songs directly, shown
 * from 5 letters (or 80% of the title for short titles).
 */
export function GuessBox({
  serverId,
  mode,
  targetTitle,
  onGuess,
  onGiveUp,
}: {
  serverId: number
  mode: GameMode
  targetTitle: string
  onGuess: (item: GameSearchItem) => Promise<boolean>
  onGiveUp: () => void
}) {
  const [q, setQ] = useState('')
  const [artist, setArtist] = useState<GameSearchItem | null>(null)
  const [wrong, setWrong] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const text = q.trim()
  const songsFrom = Math.max(3, Math.min(5, Math.ceil(targetTitle.length * 0.8)))

  const nameKind: GameMode = mode === 'album' ? 'album' : 'artist'
  const names = useGameSearch(serverId, nameKind, text, null, text.length >= 3 && !(mode === 'track' && artist))
  const songs = useGameSearch(
    serverId,
    'track',
    text,
    artist?.rating_key ?? null,
    mode === 'track' && (artist !== null || text.length >= songsFrom),
  )

  const pick = async (item: GameSearchItem) => {
    if (mode === 'track' && item.kind === 'artist') {
      setArtist(item)
      setQ('')
      return
    }
    setChecking(true)
    try {
      const right = await onGuess(item)
      if (!right) {
        setWrong(item.title)
        setQ('')
      }
    } finally {
      setChecking(false)
    }
  }

  const nameResults = mode === 'track' && artist ? [] : text.length >= 3 ? (names.data ?? []) : []
  const songResults = mode === 'track' && (artist !== null || text.length >= songsFrom) ? (songs.data ?? []) : []
  const placeholder =
    mode === 'artist' ? 'Which artist is it?' : mode === 'album' ? 'Which album is it?' : artist ? `A song by ${artist.title}…` : 'Artist or song…'

  return (
    <div className="relative space-y-2">
      <div className="flex items-center gap-2">
        {artist && (
          <button
            type="button"
            onClick={() => setArtist(null)}
            aria-label={`Clear ${artist.title}`}
            className="flex shrink-0 items-center gap-1 rounded-full bg-accent/15 px-2.5 py-1 text-xs font-medium text-accent"
          >
            {artist.title} ✕
          </button>
        )}
        <input
          type="search"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setWrong(null)
          }}
          placeholder={placeholder}
          aria-label="Your guess"
          autoComplete="off"
          className={`min-w-0 flex-1 rounded-lg border bg-surface-2 px-3 py-2 text-sm ${wrong ? 'border-danger' : 'border-border'}`}
        />
        <button type="button" onClick={onGiveUp} className="shrink-0 rounded-lg px-2 py-2 text-xs font-medium text-muted hover:text-text">
          Give up
        </button>
      </div>
      {wrong && (
        <p className="text-xs text-danger" role="status">
          Not “{wrong}”: −{PENALTY} s
        </p>
      )}
      {(nameResults.length > 0 || songResults.length > 0) && (
        // Opens upwards, over the card: below would be under a phone's keyboard.
        <ul
          className="absolute inset-x-0 bottom-full z-30 mb-2 max-h-64 overflow-y-auto rounded-xl border border-border bg-surface p-1 shadow-2xl"
          aria-label="Suggestions"
        >
          {nameResults.map((item) => (
            <GuessRow key={`n${item.rating_key}`} serverId={serverId} item={item} disabled={checking} onPick={pick} hint={mode === 'track' ? 'songs →' : undefined} />
          ))}
          {songResults.map((item) => (
            <GuessRow key={`s${item.rating_key}`} serverId={serverId} item={item} disabled={checking} onPick={pick} />
          ))}
        </ul>
      )}
      {mode === 'track' && !artist && text.length >= 3 && text.length < songsFrom && (
        <p className="text-xs text-muted">Pick the artist, or type more of the song's title.</p>
      )}
    </div>
  )
}

function GuessRow({
  serverId,
  item,
  disabled,
  onPick,
  hint,
}: {
  serverId: number
  item: GameSearchItem
  disabled: boolean
  onPick: (item: GameSearchItem) => void
  hint?: string
}) {
  return (
    <li>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onPick(item)}
        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-surface-2 disabled:opacity-50"
      >
        <Artwork serverId={serverId} path={item.thumb} title={item.title} size={64} round={item.kind === 'artist'} className="size-8 shrink-0 text-xs" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm">{item.title}</span>
          {item.subtitle && <span className="block truncate text-xs text-muted">{item.subtitle}</span>}
        </span>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </button>
    </li>
  )
}
