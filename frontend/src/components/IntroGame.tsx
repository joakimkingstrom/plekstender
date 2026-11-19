import { useEffect, useRef, useState } from 'react'
import { useGameSearch, type GameMode, type GameSearchItem } from '../api/game'
import type { Feedback, GuessResult } from '../game'
import { Artwork } from './library'

/** The round's time bar over the card. */
export function RoundClock({ elapsed, length, loading }: { elapsed: number; length: number; loading: boolean }) {
  const left = Math.max(0, length - elapsed)
  return (
    <div className="space-y-1" aria-live="off">
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div
          className={`h-full rounded-full ${!loading && left <= 5 ? 'bg-danger' : 'bg-accent'} ${loading ? 'w-1/4 animate-pulse' : ''}`}
          style={loading ? undefined : { width: `${(left / length) * 100}%` }}
        />
      </div>
      <p className="text-right text-xs tabular-nums text-muted">{loading ? 'Getting the song…' : `${Math.ceil(left)} s`}</p>
    </div>
  )
}

/**
 * The guess field. Artists (and albums, in album mode) are suggested from 3 letters. For
 * songs: pick an artist first and then search their songs, or search songs directly, shown
 * from 5 letters (or 80% of the title for short titles). Suggestions close when you tap
 * elsewhere (to see the artwork), and open again when you type or tap the field.
 */
export function GuessBox({
  serverId,
  mode,
  targetTitle,
  feedback,
  guessesLeft,
  artistFeedback,
  onGuess,
  onGiveUp,
}: {
  serverId: number
  mode: GameMode
  targetTitle: string
  feedback: Feedback
  guessesLeft: number | null
  artistFeedback: boolean
  onGuess: (item: GameSearchItem) => Promise<GuessResult>
  onGiveUp: () => void
}) {
  const [q, setQ] = useState('')
  const [artist, setArtist] = useState<GameSearchItem | null>(null)
  const [open, setOpen] = useState(true)
  const [checking, setChecking] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const text = q.trim()
  const songsFrom = Math.max(3, Math.min(5, Math.ceil(targetTitle.length * 0.8)))

  // Close the suggestions on a tap or click anywhere else.
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open])

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
    setChecking(true)
    try {
      const result = await onGuess(item)
      if (mode === 'track' && item.kind === 'artist') {
        // Right artist (or no feedback): narrow the search to their songs.
        if (result.artistCorrect || !artistFeedback) setArtist(item)
        setQ('')
      } else if (!result.correct) {
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
  // Album and artist modes: no pictures in the suggestions (they'd give the answer away).
  const pictures = mode === 'track'

  return (
    <div ref={root} className="relative space-y-1.5">
      {open && (nameResults.length > 0 || songResults.length > 0) && (
        // Opens upwards, over the card: below would be under a phone's keyboard.
        <ul
          className="absolute inset-x-0 bottom-full z-30 mb-2 max-h-64 overflow-y-auto rounded-xl border border-border bg-surface p-1 shadow-2xl"
          aria-label="Suggestions"
        >
          {nameResults.map((item) => (
            <GuessRow
              key={`n${item.rating_key}`}
              serverId={serverId}
              item={item}
              picture={pictures}
              disabled={checking}
              onPick={pick}
              hint={mode === 'track' ? 'songs →' : undefined}
            />
          ))}
          {songResults.map((item) => (
            <GuessRow key={`s${item.rating_key}`} serverId={serverId} item={item} picture={pictures} disabled={checking} onPick={pick} />
          ))}
        </ul>
      )}
      {/* Full width on its own row (comfortable to tap); the artist picked and "Give up"
          sit underneath. */}
      <input
        type="search"
        value={q}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        aria-label="Your guess"
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        enterKeyHint="search"
        className={`w-full rounded-lg border bg-surface-2 px-3 py-2.5 text-sm ${feedback?.tone === 'bad' ? 'border-danger' : 'border-border'}`}
      />
      <p role="status" className={`text-xs ${feedback ? (feedback.tone === 'good' ? 'text-ok' : 'text-danger') : 'text-muted'}`}>
        {feedback?.text ??
          (mode === 'track' && !artist && text.length >= 3 && text.length < songsFrom
            ? "Pick the artist, or type more of the song's title."
            : '\u00a0')}
      </p>
      <div className="flex items-center gap-2 text-xs">
        {artist && (
          <button
            type="button"
            onClick={() => setArtist(null)}
            aria-label={`Clear ${artist.title}`}
            className="flex min-w-0 items-center gap-1 truncate rounded-full bg-accent/15 px-2.5 py-1 font-medium text-accent"
          >
            {artist.title} ✕
          </button>
        )}
        {guessesLeft !== null && (
          <span className="text-muted tabular-nums">
            {guessesLeft} {guessesLeft === 1 ? 'guess' : 'guesses'} left
          </span>
        )}
        <button
          type="button"
          onClick={onGiveUp}
          className="ml-auto shrink-0 rounded-lg px-3 py-1.5 font-medium text-muted hover:bg-surface-2 hover:text-text"
        >
          Give up
        </button>
      </div>
    </div>
  )
}

function GuessRow({
  serverId,
  item,
  picture,
  disabled,
  onPick,
  hint,
}: {
  serverId: number
  item: GameSearchItem
  picture: boolean
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
        {picture && (
          <Artwork serverId={serverId} path={item.thumb} title={item.title} size={64} round={item.kind === 'artist'} className="size-8 shrink-0 text-xs" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm">{item.title}</span>
          {item.subtitle && <span className="block truncate text-xs text-muted">{item.subtitle}</span>}
        </span>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </button>
    </li>
  )
}
