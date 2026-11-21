import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react'
import { Segmented } from '../components/Segmented'
import { Select } from '../components/Select'
import { Tooltip } from '../components/Tooltip'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useAlbum, useArtist, useTags } from '../api/library'
import {
  useRate,
  useRateQueue,
  useUndoRating,
  type RateItem,
  type RateKind,
  type RateOrder,
  type RatePool,
} from '../api/ratings'
import { Artwork, Stars } from '../components/library'
import { Mark } from '../components/Mark'
import { PlayButton } from '../components/PlayButton'
import { GameIntro, GuessBox, RoundClock } from '../components/IntroGame'
import { useGameSettings, useIntroRound } from '../game'
import { fetchSample, useAddToGamePlaylist, useGameStats } from '../api/game'
import { playSample, stopAudio, unlockAudio } from '../audio'
import { StarRating } from '../components/StarRating'
import { Badge, Button, Card, Notice, Spinner, UndoIcon } from '../components/ui'
import { formatCompact, formatCount, formatDuration } from '../format'
import { starsText, swipeResult } from '../swipe'
import { useLibraryServer } from '../components/useLibraryServer'

type Layout = 'swipe' | 'desktop'
type Settings = { kind: RateKind; pool: RatePool; order: RateOrder; layout: Layout; tag: number | null; game: boolean; autoplay: boolean }

const STORAGE_KEY = 'plekstender.rate'
const KINDS: [RateKind, string][] = [
  ['track', 'Songs'],
  ['album', 'Albums'],
  ['artist', 'Artists'],
]
const POOLS: [RatePool, string][] = [
  ['listened', 'Listened to'],
  ['unrated', 'Unrated'],
  ['all', 'Everything'],
]
const LAYOUTS: [Layout, string][] = [
  ['swipe', 'Cards'],
  ['desktop', 'Lists'],
]
const ORDERS: [RateOrder, string][] = [
  ['weighted', 'Shuffle (favours what you play)'],
  ['plays', 'Most played first'],
  ['popular', 'Most popular first'],
]

function loadSettings(): Settings {
  const wide = typeof window !== 'undefined' && window.matchMedia?.('(min-width: 768px)').matches
  const defaults: Settings = { kind: 'track', pool: 'listened', order: 'weighted', layout: wide ? 'desktop' : 'swipe', tag: null, game: false, autoplay: false }
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') }
  } catch {
    return defaults
  }
}

/** Ratings changed this session, on top of what the server sent (0-10 or null). */
type Overrides = Record<number, number | null>

type RateFn = (ratingKey: number, rating: number | null) => void

export function RatePage() {
  const { active, blocker } = useLibraryServer()
  return (
    <>
      {/* On a phone Rate fills the screen (the app's header and tab bar are hidden): a slim
          title bar with a close button back to the rest of the app. */}
      <div className="mb-3 flex items-center justify-between md:hidden">
        <span className="flex items-center gap-2 font-display text-lg font-bold">
          <Mark className="size-5" />
          Rate
        </span>
        <Link
          to="/"
          aria-label="Close Rate"
          className="flex size-9 items-center justify-center rounded-full bg-surface-2 text-lg text-muted hover:text-text"
        >
          ✕
        </Link>
      </div>
      <div className="hidden md:block">
        <p className="mb-4 text-sm text-muted">
          Ratings are saved to Plex with your own account.{' '}
          <Link to="/history/ratings" className="text-accent underline">
            Everything you've rated
          </Link>
        </p>
      </div>
      {blocker ?? <Rater serverId={active!.id} />}
    </>
  )
}

function Rater({ serverId }: { serverId: number }) {
  const [settings, setSettings] = useState<Settings>(loadSettings)
  // On a phone the game opens with how it works and a Start tap (which also lets iPhones
  // play the music); elsewhere it starts straight away.
  const [gameStarted, setGameStarted] = useState(false)
  const phone = typeof window !== 'undefined' && !window.matchMedia?.('(min-width: 768px)').matches
  const update = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch }
    setSettings(next)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // storage unavailable: the choice lasts for this visit
    }
  }
  // The Lists layout shows 15 songs at a time: keep two pages in hand.
  const keep = settings.layout === 'desktop' && settings.kind === 'track' ? SONGS_PER_PAGE * 2 : 6
  // In Guess the intro, the popularity setting picks the order: well-known first, deep cuts
  // (least popular first), or your own order ("mixed").
  const gameSettings = useGameSettings()
  const order: RateOrder =
    settings.game && settings.layout === 'swipe' && gameSettings.popularity !== 'mixed'
      ? gameSettings.popularity === 'deep'
        ? 'obscure'
        : 'popular'
      : settings.order
  const queue = useRateQueue(serverId, settings.kind, settings.pool, order, keep, settings.tag)
  const genres = useTags(serverId, 'genre')
  const styles = useTags(serverId, 'style')
  const moods = useTags(serverId, 'mood')
  const rateMutation = useRate(serverId)
  const undoMutation = useUndoRating(serverId)
  const [overrides, setOverrides] = useState<Overrides>({})
  const [error, setError] = useState<string | null>(null)
  const [canUndo, setCanUndo] = useState(false)
  // Phones: genre, pool, order and layout fold away so the card fits the screen.
  const [optionsOpen, setOptionsOpen] = useState(false)
  // Items swiped away, so an undo can bring the card back.
  const swiped = useRef<RateItem[]>([])

  const rate: RateFn = useCallback(
    (ratingKey, rating) => {
      setError(null)
      let previous: number | null | undefined
      setOverrides((o) => {
        previous = o[ratingKey]
        return { ...o, [ratingKey]: rating }
      })
      rateMutation.mutate(
        { rating_key: ratingKey, rating },
        {
          onSuccess: () => setCanUndo(true),
          onError: (e) => {
            setOverrides((o) => {
              const next = { ...o }
              if (previous === undefined) delete next[ratingKey]
              else next[ratingKey] = previous
              return next
            })
            setError(errorMessage(e))
          },
        },
      )
    },
    [rateMutation],
  )

  const undo = () => {
    setError(null)
    undoMutation.mutate(undefined, {
      onSuccess: (result) => {
        setOverrides((o) => ({ ...o, [result.rating_key]: result.user_rating }))
        const last = swiped.current.at(-1)
        if (last?.rating_key === result.rating_key) {
          swiped.current.pop()
          queue.putBack(last)
        }
      },
      onError: (e) => {
        setCanUndo(false)
        setError(errorMessage(e))
      },
    })
  }

  const props: ViewProps = { serverId, queue, overrides, rate, settings }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented
          label="What to rate"
          value={settings.kind}
          options={KINDS}
          onChange={(kind) => update({ kind })}
          className="max-md:flex-1"
        />
        <Button
          variant="secondary"
          size="none" className="border border-border px-3 py-[7px] text-sm md:hidden"
          aria-expanded={optionsOpen}
          aria-controls="rate-options"
          onClick={() => setOptionsOpen((o) => !o)}
        >
          Options
          <svg viewBox="0 0 12 12" className={`size-3 text-muted transition-transform ${optionsOpen ? 'rotate-180' : ''}`} aria-hidden>
            <path d="M2 4.5l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </Button>
        <div
          id="rate-options"
          className={`${optionsOpen ? 'flex' : 'hidden'} w-full flex-wrap items-center gap-2 md:flex md:w-auto`}
        >
          <Select
            label="Genre, style or mood"
            value={settings.tag ?? 0}
            onChange={(tag) => update({ tag: tag || null })}
            options={[
              { value: 0, label: 'All genres' },
              ...(genres.data ?? []).map((g) => ({ value: g.id, label: g.name, group: 'Genres' })),
              ...(styles.data ?? []).map((g) => ({ value: g.id, label: g.name, group: 'Styles' })),
              ...(moods.data ?? []).map((g) => ({ value: g.id, label: g.name, group: 'Moods' })),
            ]}
          />
          <Select
            label="Which ones"
            value={settings.pool}
            onChange={(pool) => update({ pool })}
            options={POOLS.map(([value, label]) => ({ value, label }))}
          />
          <Select
            label="Order"
            value={settings.order}
            onChange={(order) => update({ order })}
            options={ORDERS.map(([value, label]) => ({ value, label }))}
          />
          <div className="md:hidden">
            <Segmented label="Layout" value={settings.layout} options={LAYOUTS} onChange={(layout) => update({ layout })} />
          </div>
          {settings.layout === 'swipe' && !settings.game && (
            <Tooltip content="Play each card's song as it comes up (for albums and artists, one of their most popular songs).">
              <button
                type="button"
                aria-pressed={settings.autoplay}
                onClick={() => {
                  unlockAudio() // a tap: lets the songs play on iPhones
                  if (settings.autoplay) stopAudio()
                  update({ autoplay: !settings.autoplay })
                }}
                className={`rounded-lg border px-3 py-[7px] text-sm font-medium ${
                  settings.autoplay ? 'border-accent bg-accent/15 text-accent' : 'border-border bg-surface-2 text-muted hover:text-text'
                }`}
              >
                ▶ Play songs
              </button>
            </Tooltip>
          )}
          {settings.layout === 'swipe' && (
            <Tooltip content="A song plays with the card hidden: guess it within 30 seconds, then rate it.">
              <button
                type="button"
                aria-pressed={settings.game}
                onClick={() => {
                  unlockAudio() // a tap: lets the game play sound on iPhones
                  setGameStarted(false)
                  update({ game: !settings.game })
                }}
                className={`rounded-lg border px-3 py-[7px] text-sm font-medium ${
                  settings.game ? 'border-accent bg-accent/15 text-accent' : 'border-border bg-surface-2 text-muted hover:text-text'
                }`}
              >
                ♪ Guess the intro
              </button>
            </Tooltip>
          )}
        </div>
        <div className="flex items-center gap-2 md:ml-auto">
          <Tooltip content="Undo your last rating (repeat to go further back)">
            <Button variant="ghost" onClick={undo} disabled={!canUndo} busy={undoMutation.isPending} aria-label="Undo">
              <UndoIcon className="size-4" />
              <span className="max-md:hidden">Undo</span>
            </Button>
          </Tooltip>
          <div className="hidden md:block">
            <Segmented label="Layout" value={settings.layout} options={LAYOUTS} onChange={(layout) => update({ layout })} />
          </div>
        </div>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {queue.error ? (
        <Notice tone="error">
          {errorMessage(queue.error)}{' '}
          <button type="button" className="underline" onClick={queue.retry}>
            Try again
          </button>
        </Notice>
      ) : queue.items.length === 0 ? (
        queue.loading || !queue.exhausted ? (
          <Spinner />
        ) : (
          <Card>
            <p className="text-sm text-muted">
              Nothing left to rate here. Try another selection{settings.pool === 'listened' ? ', or import your listening history on ' : '.'}
              {settings.pool === 'listened' && (
                <Link to="/settings/listening" className="text-accent underline">
                  Settings → Listening
                </Link>
              )}
            </p>
          </Card>
        )
      ) : settings.layout === 'swipe' && settings.game && phone && !gameStarted ? (
        <GameIntro
          serverId={serverId}
          mode={settings.kind}
          firstKey={props.queue.items[0]?.rating_key ?? null}
          onStart={() => {
            unlockAudio()
            setGameStarted(true)
          }}
          onCancel={() => update({ game: false })}
        />
      ) : settings.layout === 'swipe' ? (
        <SwipeView
          {...props}
          onSwiped={(item) => swiped.current.push(item)}
          setAutoplay={(autoplay) => update({ autoplay })}
        />
      ) : settings.kind === 'track' ? (
        <SongsGrid {...props} />
      ) : settings.kind === 'album' ? (
        <AlbumRater {...props} />
      ) : (
        <ArtistRater {...props} />
      )}
    </div>
  )
}

type ViewProps = {
  serverId: number
  queue: ReturnType<typeof useRateQueue>
  overrides: Overrides
  rate: RateFn
  settings: Settings
}

function current(item: { rating_key: number; user_rating: number | null }, overrides: Overrides) {
  return item.rating_key in overrides ? overrides[item.rating_key] : item.user_rating
}

function details(item: RateItem): string {
  const parts =
    item.kind === 'track'
      ? [item.subtitle, item.album_title, item.year]
      : item.kind === 'album'
        ? [item.subtitle, item.year]
        : []
  return parts.filter(Boolean).join(' · ')
}

// --- swipe cards ---------------------------------------------------------------------

const HINT_KEY = 'plekstender.rate.swiped'
/** Unplayable cards skipped in a row before the game stops to say so. */
const MAX_UNPLAYABLE = 8

function readFlag(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeFlag(key: string) {
  try {
    localStorage.setItem(key, '1')
  } catch {
    // storage unavailable: the hint shows again next time
  }
}

function SwipeView({
  serverId,
  queue,
  overrides,
  rate,
  onSwiped,
  settings,
  setAutoplay,
}: ViewProps & { onSwiped: (item: RateItem) => void; setAutoplay: (on: boolean) => void }) {
  const item = queue.items[0]
  const next = queue.items[1]
  const game = settings.game
  // Cards the game can't play (silent intro, very short title, no file) are skipped, but
  // not forever: after a run of them, say so instead.
  const [unplayable, setUnplayable] = useState(0)
  const skippedInARow = useRef(0)
  const gameSettings = useGameSettings()
  const playlistAdd = useAddToGamePlaylist(serverId)
  const round = useIntroRound({
    settings: gameSettings,
    active: game,
    serverId,
    mode: settings.kind,
    ratingKey: item.rating_key,
    nextKey: next?.rating_key ?? null,
    onUnplayable: () => {
      skippedInARow.current += 1
      setUnplayable(skippedInARow.current)
      if (skippedInARow.current < MAX_UNPLAYABLE) queue.advance()
    },
  })
  const keepGoing = () => {
    skippedInARow.current = 0
    setUnplayable(0)
    queue.advance()
  }
  const stats = useGameStats()
  const best = stats.data?.find((s) => s.mode === settings.kind)?.best_streak ?? 0
  // While guessing, the card can't be swiped (rating comes after the answer).
  const guessing = game && round.playing
  const autoplay = settings.autoplay && !game

  // "Play songs": each card's song starts as the card comes up; the next one is prepared.
  const nextKey = next?.rating_key ?? null
  useEffect(() => {
    if (!autoplay) return
    void playSample(serverId, settings.kind, item.rating_key)
    if (nextKey !== null) void fetchSample(serverId, settings.kind, nextKey).catch(() => undefined)
  }, [autoplay, serverId, settings.kind, item.rating_key, nextKey])
  // Leaving the card view stops whatever plays.
  useEffect(() => () => stopAudio(), [])
  const cardRef = useRef<HTMLDivElement>(null)
  const start = useRef<{ x: number; y: number } | null>(null)
  const lastRating = useRef<number | null>(null)
  const [drag, setDrag] = useState({ dx: 0, dy: 0 })
  const [dragging, setDragging] = useState(false)
  const [width, setWidth] = useState(360)
  const [leaving, setLeaving] = useState<'left' | 'right' | 'down' | null>(null)
  const [hint, setHint] = useState(() => readFlag(HINT_KEY) === null)
  const result = swipeResult(drag.dx, drag.dy, width)

  const next_ = () => {
    if (!autoplay) stopAudio() // (with autoplay, the next card's song takes over)
    if (round.phase === 'won' || round.phase === 'lost') {
      skippedInARow.current = 0
      setUnplayable(0)
    }
    queue.advance()
  }

  const finish = (action: 'rate' | 'skip', rating?: number) => {
    if (hint) {
      setHint(false)
      writeFlag(HINT_KEY)
    }
    setLeaving(action === 'skip' ? 'down' : drag.dx > 0 ? 'right' : 'left')
    window.setTimeout(() => {
      if (action === 'rate' && rating !== undefined) {
        rate(item.rating_key, rating)
        onSwiped(item)
      }
      next_()
      setLeaving(null)
      setDrag({ dx: 0, dy: 0 })
    }, 180)
  }

  const onPointerDown = (e: PointerEvent) => {
    if (leaving || guessing) return
    start.current = { x: e.clientX, y: e.clientY }
    lastRating.current = null
    setWidth(cardRef.current?.clientWidth ?? 360)
    setDragging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: PointerEvent) => {
    if (!start.current) return
    const dx = e.clientX - start.current.x
    const dy = e.clientY - start.current.y
    setDrag({ dx, dy })
    const r = swipeResult(dx, dy, width)
    const value = r.kind === 'rate' ? r.rating : null
    if (value !== lastRating.current) {
      lastRating.current = value
      if (value !== null) navigator.vibrate?.(8) // a tick per half star
    }
  }
  const onPointerUp = () => {
    if (!start.current) return
    start.current = null
    setDragging(false)
    if (result.kind === 'rate') finish('rate', result.rating)
    else if (result.kind === 'skip') finish('skip')
    else setDrag({ dx: 0, dy: 0 })
  }

  const value = current(item, overrides)
  const offset = leaving
    ? leaving === 'down'
      ? 'translate(0, 120vh)'
      : `translate(${leaving === 'right' ? '' : '-'}120vw, ${drag.dy}px) rotate(${leaving === 'right' ? 20 : -20}deg)`
    : `translate(${drag.dx}px, ${Math.max(0, drag.dy)}px) rotate(${drag.dx / 20}deg)`
  const tint =
    result.kind === 'rate' ? (result.rating >= 5 ? 'ring-accent' : 'ring-danger') : result.kind === 'skip' ? 'ring-muted' : 'ring-transparent'

  return (
    // As wide as fits the screen's height too, so the card and the stars need no scrolling.
    <div className="mx-auto space-y-3" style={{ maxWidth: 'min(24rem, max(15rem, calc(100dvh - 21rem)))' }}>
      {game && (
        <div className="flex items-center justify-between gap-2 text-xs text-muted" aria-live="polite">
          <span>
            Score <span className="font-semibold text-text tabular-nums">{round.session.score}</span> · Streak{' '}
            <span className="font-semibold text-text tabular-nums">{round.session.streak}</span>
          </span>
          <span>
            Best streak {best} ·{' '}
            <Link to="/history/scores" className="underline hover:text-text">
              Scores
            </Link>
          </span>
        </div>
      )}
      {game && unplayable >= MAX_UNPLAYABLE && (
        <Notice tone="warn">
          The last {MAX_UNPLAYABLE} cards couldn't be played (silent intros, very short titles or no file in Plex). Try
          another selection, or{' '}
          <button type="button" className="underline" onClick={keepGoing}>
            keep going
          </button>
          .
        </Notice>
      )}
      {round.error && game && <Notice tone="error">{round.error}</Notice>}
      {guessing && <RoundClock elapsed={round.elapsed} length={round.length} loading={round.phase === 'loading'} />}
      {game && round.phase === 'blocked' && (
        <Button variant="primary" size="lg" className="w-full" onClick={round.resume}>
          ▶ Tap to play the song
        </Button>
      )}
      {hint && !game && (
        <p className="text-center text-xs text-muted">
          Swipe right for 2½–5 stars, left for 2–½, down to skip. The further you swipe, the higher or lower.
        </p>
      )}
      <div className="relative">
        {next && (
          <div className="absolute inset-x-3 top-3 rounded-2xl border border-border bg-surface p-4 opacity-60" aria-hidden>
            {/* In the game the next card is a puzzle too: hidden like a new round. */}
            <div className={`overflow-hidden ${next.kind === 'artist' ? 'rounded-full' : 'rounded-lg'}`}>
              <Artwork
                serverId={serverId}
                path={next.thumb}
                title={game ? '?' : next.title}
                size={400}
                round={next.kind === 'artist'}
                className="w-full"
                style={game ? { filter: 'blur(28px)', transform: 'scale(1.08)' } : undefined}
              />
            </div>
          </div>
        )}
        <div
          ref={cardRef}
          key={item.rating_key}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          className={`relative cursor-grab touch-none select-none rounded-2xl border border-border bg-surface p-4 shadow-xl ring-4 ${tint} active:cursor-grabbing`}
          style={{
            transform: offset,
            transition: dragging ? 'none' : 'transform 180ms ease-out',
          }}
        >
          <div className={`overflow-hidden ${item.kind === 'artist' ? 'rounded-full' : 'rounded-lg'}`}>
            <Artwork
              serverId={serverId}
              path={item.thumb}
              title={game && !round.reveal.title ? '?' : item.title}
              size={600}
              round={item.kind === 'artist'}
              className="pointer-events-none w-full"
              style={game ? { filter: `blur(${round.reveal.blur}px)`, transform: 'scale(1.08)', transition: 'filter 150ms linear' } : undefined}
            />
          </div>
          {game && (round.phase === 'won' || round.phase === 'lost') && (
            <div
              className={`mt-3 rounded-lg px-3 py-2 text-center text-sm font-semibold ${
                round.phase === 'won' ? 'bg-ok/15 text-ok' : 'bg-surface-2 text-muted'
              }`}
              role="status"
            >
              {round.phase === 'won'
                ? `Correct! +${round.points}`
                : `${round.feedback?.text.endsWith('No guesses left.') ? 'Out of guesses' : "Time's up"}${round.points ? ` · +${round.points} for the artist` : ''}`}
            </div>
          )}
          <div className="mt-4 space-y-1">
            <h2 className="text-xl font-bold leading-tight">{!game || round.reveal.title ? item.title : '? ? ?'}</h2>
            <p className="text-sm text-muted">{!game || round.reveal.artist ? details(item) || '\u00a0' : '\u00a0'}</p>
            {game && round.reveal.song && (
              <p className="text-sm">
                <span className="text-muted">Playing: </span>
                {round.reveal.song}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2 pt-1 text-xs text-muted">
              {value !== null && value !== undefined && <Badge tone="accent">Rated {starsText(value)} ★</Badge>}
              {!guessing && <span>{formatCount(item.plays)} plays</span>}
              {!guessing && item.popularity !== null && <span>Popularity {formatCompact(item.popularity)}</span>}
              {!game && (
                <span className="ml-auto">
                  {/* Play keeps playing: every next card's song too, until stopped. */}
                  <PlayButton
                    serverId={serverId}
                    ratingKey={item.rating_key}
                    title={item.title}
                    size="size-8"
                    kind={settings.kind}
                    onPlay={() =>
                      // Already on (this card's clip ended): play it again; otherwise switch on,
                      // which plays this card and the ones after it.
                      settings.autoplay ? void playSample(serverId, settings.kind, item.rating_key) : setAutoplay(true)
                    }
                    onStop={() => setAutoplay(false)}
                  />
                </span>
              )}
            </div>
            {game && (round.phase === 'won' || round.phase === 'lost') && (
              <div className="pt-2" onPointerDown={(e) => e.stopPropagation()}>
                {playlistAdd.isSuccess && playlistAdd.variables?.target === item.rating_key ? (
                  <p className="text-xs text-ok" role="status">
                    {playlistAdd.data.added.length
                      ? `Added ${playlistAdd.data.added.length} songs to “${playlistAdd.data.playlist}”.`
                      : `Already in “${playlistAdd.data.playlist}”.`}
                  </p>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm" className="w-full"
                    busy={playlistAdd.isPending}
                    onClick={() => playlistAdd.mutate({ mode: settings.kind, target: item.rating_key })}
                  >
                    ＋ Top 5 songs by {item.kind === 'artist' ? item.title : item.subtitle ?? 'this artist'} to “{gameSettings.playlist}”
                  </Button>
                )}
                {playlistAdd.isError && playlistAdd.variables?.target === item.rating_key && (
                  <p className="pt-1 text-xs text-danger">{errorMessage(playlistAdd.error)}</p>
                )}
              </div>
            )}
          </div>
        </div>
        {/* Stays put while the card moves, so the rating is always readable. */}
        {result.kind !== 'none' && (
          <div className="pointer-events-none absolute inset-x-0 top-[28%] z-20 flex justify-center" aria-live="polite">
            <div className="flex flex-col items-center rounded-2xl border border-border bg-bg/90 px-6 py-4 shadow-2xl backdrop-blur-sm">
              {result.kind === 'rate' ? (
                <>
                  <Stars rating={result.rating} className="[&_svg]:size-8" />
                  <span className="mt-1 font-display text-2xl font-bold">{starsText(result.rating)} ★</span>
                </>
              ) : (
                <span className="font-display text-2xl font-bold">Skip</span>
              )}
            </div>
          </div>
        )}
      </div>
      {guessing ? (
        <GuessBox
          key={item.rating_key}
          serverId={serverId}
          mode={settings.kind}
          targetTitle={item.title}
          feedback={round.feedback}
          guessesLeft={round.guessesLeft}
          artistFeedback={gameSettings.artist_feedback}
          onGuess={round.guess}
          onGiveUp={round.giveUp}
        />
      ) : (
        <div className="flex items-center justify-between gap-3">
          <StarRating
            label={`Rate ${item.title}`}
            value={value}
            size="size-7"
            onChange={(rating) => {
              rate(item.rating_key, rating)
              if (rating !== null) {
                onSwiped(item)
                next_()
              }
            }}
          />
          <Button variant="ghost" onClick={next_}>
            {game ? 'Next' : 'Skip'}
          </Button>
        </div>
      )}
    </div>
  )
}

// --- desktop: songs ----------------------------------------------------------------------

/** Songs per page in the Lists layout. */
const SONGS_PER_PAGE = 15
const ROW_MIN = 40
const ROW_MAX = 60

function SongsGrid({ serverId, queue, overrides, rate }: ViewProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [rowHeight, setRowHeight] = useState(ROW_MAX)
  const [skipped, setSkipped] = useState<Set<number>>(new Set())

  // Always 15 songs; the rows get shorter to fit the window (down to a minimum, below which
  // the page scrolls on very small screens).
  useLayoutEffect(() => {
    const measure = () => {
      const top = ref.current?.getBoundingClientRect().top ?? 200
      const footer = 72 + (window.innerWidth < 768 ? 64 : 0)
      const fit = Math.floor((window.innerHeight - top - footer) / SONGS_PER_PAGE)
      setRowHeight(Math.max(ROW_MIN, Math.min(ROW_MAX, fit)))
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])

  const page = queue.items.slice(0, SONGS_PER_PAGE)
  const handled = (key: number) => key in overrides || skipped.has(key)
  const done = page.filter((i) => handled(i.rating_key)).length
  const pageKey = page.map((i) => i.rating_key).join(',')

  const next = useCallback(() => {
    queue.advance(page.length)
    setSkipped(new Set())
  }, [queue, page.length])

  // Everything on the page rated or skipped: move on to the next songs.
  useEffect(() => {
    if (page.length === 0 || done < page.length) return
    const timer = window.setTimeout(next, 700)
    return () => window.clearTimeout(timer)
  }, [done, page.length, pageKey, next])

  const skip = (key: number) => setSkipped((s) => new Set(s).add(key))

  const focusRow = (index: number) => {
    const target = ref.current?.querySelectorAll<HTMLElement>('[role="slider"]')[index]
    target?.focus()
  }

  return (
    <div ref={ref}>
      <ol className="divide-y divide-border rounded-2xl border border-border bg-surface">
        {page.map((item, index) => {
          const value = current(item, overrides)
          const isSkipped = skipped.has(item.rating_key) && !(item.rating_key in overrides)
          return (
            <li
              key={item.rating_key}
              className={`flex items-center gap-3 px-3 ${item.rating_key in overrides ? 'bg-accent/5' : ''} ${isSkipped ? 'opacity-40' : ''}`}
              style={{ height: rowHeight }}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  focusRow(Math.min(page.length - 1, index + 1))
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  focusRow(Math.max(0, index - 1))
                } else if (e.key === 's' || e.key === 'S') {
                  e.preventDefault()
                  skip(item.rating_key)
                  focusRow(Math.min(page.length - 1, index + 1))
                }
              }}
            >
              <Artwork
                serverId={serverId}
                path={item.thumb}
                title={item.title}
                size={96}
                className="shrink-0"
                style={{ width: rowHeight - 16, height: rowHeight - 16 }}
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{item.title}</p>
                <p className="truncate text-xs text-muted">{details(item)}</p>
              </div>
              <Tooltip content="Plays (Plex or your listening history, whichever is higher)" className="hidden w-20 justify-end sm:inline-flex">
                <span className="text-xs text-muted tabular-nums">{formatCount(item.plays)} plays</span>
              </Tooltip>
              <span className="hidden w-10 text-right text-xs text-muted tabular-nums md:block">{formatDuration(item.duration_ms)}</span>
              <PlayButton serverId={serverId} ratingKey={item.rating_key} title={item.title} size="size-7" />
              <StarRating label={`Rate ${item.title}`} value={value} onChange={(rating) => rate(item.rating_key, rating)} />
              <button
                type="button"
                onClick={() => skip(item.rating_key)}
                disabled={isSkipped}
                aria-label={`Skip ${item.title}`}
                className="rounded px-2 py-1 text-xs text-muted hover:bg-surface-2 hover:text-text disabled:invisible"
              >
                Skip
              </button>
            </li>
          )
        })}
      </ol>
      <div className="mt-3 flex items-center justify-between gap-3 text-sm text-muted">
        <span>
          {done} of {page.length} done · keys 1–5 (Shift for halves), S to skip, ↑↓ to move
        </span>
        <Button variant={done === page.length ? 'primary' : 'secondary'} onClick={next}>
          Next {SONGS_PER_PAGE} songs
        </Button>
      </div>
    </div>
  )
}

// --- desktop: albums and artists ----------------------------------------------------------

/** Artwork you can click to rate: left edge = 1 star, right edge = 5, in half stars. */
function CoverRating({
  children,
  value,
  onChange,
  label,
}: {
  children: ReactNode
  value: number | null | undefined
  onChange: (rating: number) => void
  label: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const ratingAt = (e: { clientX: number; currentTarget: HTMLElement }) => {
    const box = e.currentTarget.getBoundingClientRect()
    const f = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width))
    return 2 + Math.round(f * 8) // 1-5 stars in half-star steps
  }
  return (
    <div
      className="group relative cursor-pointer"
      role="group"
      aria-label={label}
      onMouseMove={(e) => setHover(ratingAt(e))}
      onMouseLeave={() => setHover(null)}
      onClick={(e) => onChange(ratingAt(e))}
    >
      {children}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center gap-1 rounded-b-lg bg-gradient-to-t from-black/80 to-transparent px-3 pb-3 pt-10 opacity-0 transition-opacity group-hover:opacity-100">
        <Stars rating={hover ?? value ?? 0} className="text-accent [&_svg]:size-7" />
        <span className="text-xs font-medium text-white">{hover ? `${starsText(hover)} ★ — click to rate` : 'Click to rate'}</span>
      </div>
    </div>
  )
}

function NextBar({ onNext, label }: { onNext: () => void; label: string }) {
  return (
    <div className="flex justify-end">
      <Button variant="primary" onClick={onNext}>
        {label} →
      </Button>
    </div>
  )
}

function AlbumRater({ serverId, queue, overrides, rate }: ViewProps) {
  const item = queue.items[0]
  const album = useAlbum(serverId, item.rating_key)
  const value = current(item, overrides)
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,20rem)_1fr]">
        <div className="space-y-3">
          <CoverRating label={`Rate ${item.title}`} value={value} onChange={(r) => rate(item.rating_key, r)}>
            <Artwork serverId={serverId} path={item.thumb} title={item.title} size={600} className="w-full" />
          </CoverRating>
          <div>
            <h2 className="text-2xl font-bold leading-tight">{item.title}</h2>
            <p className="text-sm text-muted">{details(item)}</p>
          </div>
          <StarRating label={`Rate the album ${item.title}`} value={value} size="size-7" onChange={(r) => rate(item.rating_key, r)} />
          <p className="text-xs text-muted">
            {formatCount(item.plays)} plays
            {item.critic_rating !== null && ` · critics ${item.critic_rating.toFixed(1)}/10`}
          </p>
        </div>
        <div className="min-w-0 space-y-4">
          {item.summary && (
            <Card title="Review">
              <p className="max-h-48 overflow-y-auto whitespace-pre-line text-sm text-muted">{item.summary}</p>
            </Card>
          )}
          <Card title="Songs">
            {album.isPending ? (
              <Spinner small />
            ) : album.isError ? (
              <Notice tone="error">{errorMessage(album.error)}</Notice>
            ) : (
              <ol className="divide-y divide-border">
                {album.data.tracks.map((t) => (
                  <li key={t.rating_key} className="flex items-center gap-3 py-2 text-sm">
                    <span className="w-6 text-right text-xs text-muted tabular-nums">{t.number ?? ''}</span>
                    <span className="min-w-0 flex-1 truncate">{t.title}</span>
                    <span className="w-10 text-right text-xs text-muted tabular-nums">{formatDuration(t.duration_ms)}</span>
                    <StarRating label={`Rate ${t.title}`} value={current(t, overrides)} onChange={(r) => rate(t.rating_key, r)} />
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
      </div>
      <NextBar onNext={() => queue.advance()} label="Next album" />
    </div>
  )
}

function Biography({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="text-sm text-muted">
      <p className={`whitespace-pre-line ${open ? '' : 'line-clamp-6'}`}>{text}</p>
      <button type="button" className="mt-1 text-xs text-accent" onClick={() => setOpen(!open)}>
        {open ? 'Less' : 'More'}
      </button>
    </div>
  )
}

function ArtistRater({ serverId, queue, overrides, rate }: ViewProps) {
  const item = queue.items[0]
  const artist = useArtist(serverId, item.rating_key)
  const value = current(item, overrides)
  const albums = artist.data
    ? [...artist.data.albums].sort((a, b) => (b.popularity ?? 0) - (a.popularity ?? 0)).slice(0, 6)
    : []
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,16rem)_1fr]">
        <div className="space-y-3">
          <CoverRating label={`Rate ${item.title}`} value={value} onChange={(r) => rate(item.rating_key, r)}>
            <Artwork serverId={serverId} path={item.thumb} title={item.title} size={600} round className="w-full" />
          </CoverRating>
          <h2 className="text-2xl font-bold leading-tight">{item.title}</h2>
          <StarRating label={`Rate the artist ${item.title}`} value={value} size="size-7" onChange={(r) => rate(item.rating_key, r)} />
          <p className="text-xs text-muted">{formatCount(item.plays)} plays</p>
          {item.summary && <Biography key={item.rating_key} text={item.summary} />}
        </div>
        <div className="min-w-0 space-y-4">
          {artist.isPending ? (
            <Spinner small />
          ) : artist.isError ? (
            <Notice tone="error">{errorMessage(artist.error)}</Notice>
          ) : (
            <>
              <Card title="Most popular albums">
                <ul className="grid grid-cols-3 gap-3 sm:grid-cols-6">
                  {albums.map((al) => (
                    <li key={al.rating_key} className="min-w-0 space-y-1.5">
                      <Artwork serverId={serverId} path={al.thumb} title={al.title} size={256} className="w-full" />
                      <p className="truncate text-sm font-medium">{al.title}</p>
                      <StarRating label={`Rate ${al.title}`} value={current(al, overrides)} size="size-4" onChange={(r) => rate(al.rating_key, r)} />
                    </li>
                  ))}
                </ul>
              </Card>
              <Card title="Most popular songs">
                <ol className="divide-y divide-border">
                  {artist.data.top_tracks.slice(0, 5).map((t, i) => (
                    <li key={t.rating_key} className="flex items-center gap-3 py-2 text-sm">
                      <span className="w-4 text-right text-xs text-muted tabular-nums">{i + 1}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">{t.title}</span>
                        <span className="block truncate text-xs text-muted">{t.album_title}</span>
                      </span>
                      <StarRating label={`Rate ${t.title}`} value={current(t, overrides)} onChange={(r) => rate(t.rating_key, r)} />
                    </li>
                  ))}
                </ol>
              </Card>
            </>
          )}
        </div>
      </div>
      <NextBar onNext={() => queue.advance()} label="Next artist" />
    </div>
  )
}

