import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useAlbum, useArtist } from '../api/library'
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
import { StarRating } from '../components/StarRating'
import { Badge, Button, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { formatCompact, formatCount, formatDuration } from '../format'
import { starsText, swipeResult } from '../swipe'
import { useLibraryServer } from '../components/useLibraryServer'

type Layout = 'swipe' | 'desktop'
type Settings = { kind: RateKind; pool: RatePool; order: RateOrder; layout: Layout }

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
const ORDERS: [RateOrder, string][] = [
  ['weighted', 'Shuffle (favours what you play)'],
  ['plays', 'Most played first'],
  ['popular', 'Most popular first'],
]

function loadSettings(): Settings {
  const wide = typeof window !== 'undefined' && window.matchMedia?.('(min-width: 768px)').matches
  const defaults: Settings = { kind: 'track', pool: 'listened', order: 'weighted', layout: wide ? 'desktop' : 'swipe' }
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
      <PageTitle subtitle="Ratings are saved to Plex with your own account.">Rate</PageTitle>
      {blocker ?? <Rater serverId={active!.id} />}
    </>
  )
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: [T, string][]
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div className="inline-flex rounded-lg bg-surface-2 p-0.5" role="group" aria-label={label}>
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          aria-pressed={key === value}
          onClick={() => onChange(key)}
          className={`rounded-md px-3 py-1.5 text-sm font-medium ${
            key === value ? 'bg-surface text-text shadow-sm' : 'text-muted hover:text-text'
          }`}
        >
          {text}
        </button>
      ))}
    </div>
  )
}

function Rater({ serverId }: { serverId: number }) {
  const [settings, setSettings] = useState<Settings>(loadSettings)
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
  const queue = useRateQueue(serverId, settings.kind, settings.pool, settings.order, keep)
  const rateMutation = useRate(serverId)
  const undoMutation = useUndoRating(serverId)
  const [overrides, setOverrides] = useState<Overrides>({})
  const [error, setError] = useState<string | null>(null)
  const [canUndo, setCanUndo] = useState(false)
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
        <Segmented label="What to rate" value={settings.kind} options={KINDS} onChange={(kind) => update({ kind })} />
        <select
          aria-label="Which ones"
          value={settings.pool}
          onChange={(e) => update({ pool: e.target.value as RatePool })}
          className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
        >
          {POOLS.map(([key, text]) => (
            <option key={key} value={key}>
              {text}
            </option>
          ))}
        </select>
        <select
          aria-label="Order"
          value={settings.order}
          onChange={(e) => update({ order: e.target.value as RateOrder })}
          className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
        >
          {ORDERS.map(([key, text]) => (
            <option key={key} value={key}>
              {text}
            </option>
          ))}
        </select>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" onClick={undo} disabled={!canUndo} busy={undoMutation.isPending} title="Undo the last rating">
            ↶ Undo
          </Button>
          <Segmented
            label="Layout"
            value={settings.layout}
            options={[
              ['swipe', 'Cards'],
              ['desktop', 'Lists'],
            ]}
            onChange={(layout) => update({ layout })}
          />
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
      ) : settings.layout === 'swipe' ? (
        <SwipeView {...props} onSwiped={(item) => swiped.current.push(item)} />
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

function SwipeView({ serverId, queue, overrides, rate, onSwiped }: ViewProps & { onSwiped: (item: RateItem) => void }) {
  const item = queue.items[0]
  const next = queue.items[1]
  const cardRef = useRef<HTMLDivElement>(null)
  const start = useRef<{ x: number; y: number } | null>(null)
  const lastRating = useRef<number | null>(null)
  const [drag, setDrag] = useState({ dx: 0, dy: 0 })
  const [dragging, setDragging] = useState(false)
  const [width, setWidth] = useState(360)
  const [leaving, setLeaving] = useState<'left' | 'right' | 'down' | null>(null)
  const result = swipeResult(drag.dx, drag.dy, width)

  const finish = (action: 'rate' | 'skip', rating?: number) => {
    setLeaving(action === 'skip' ? 'down' : drag.dx > 0 ? 'right' : 'left')
    window.setTimeout(() => {
      if (action === 'rate' && rating !== undefined) {
        rate(item.rating_key, rating)
        onSwiped(item)
      }
      queue.advance()
      setLeaving(null)
      setDrag({ dx: 0, dy: 0 })
    }, 180)
  }

  const onPointerDown = (e: PointerEvent) => {
    if (leaving) return
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
    <div className="mx-auto max-w-sm space-y-4">
      <p className="text-center text-xs text-muted">
        Swipe right for 2½–5 stars, left for 2–½, down to skip. The further you swipe, the higher or lower.
      </p>
      <div className="relative" style={{ minHeight: '28rem' }}>
        {next && (
          <div className="absolute inset-x-3 top-3 rounded-2xl border border-border bg-surface p-4 opacity-60" aria-hidden>
            <Artwork serverId={serverId} path={next.thumb} title={next.title} size={400} round={next.kind === 'artist'} className="w-full" />
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
          <Artwork
            serverId={serverId}
            path={item.thumb}
            title={item.title}
            size={600}
            round={item.kind === 'artist'}
            className="pointer-events-none w-full"
          />
          {result.kind !== 'none' && (
            <div className="pointer-events-none absolute inset-x-4 top-4 flex flex-col items-center rounded-xl bg-bg/85 py-4" aria-live="polite">
              {result.kind === 'rate' ? (
                <>
                  <Stars rating={result.rating} className="[&_svg]:size-8" />
                  <span className="mt-1 font-display text-2xl font-bold">{starsText(result.rating)} ★</span>
                </>
              ) : (
                <span className="font-display text-2xl font-bold">Skip</span>
              )}
            </div>
          )}
          <div className="mt-4 space-y-1">
            <h2 className="text-xl font-bold leading-tight">{item.title}</h2>
            <p className="text-sm text-muted">{details(item)}</p>
            <div className="flex flex-wrap items-center gap-2 pt-1 text-xs text-muted">
              {value !== null && value !== undefined && <Badge tone="accent">Rated {starsText(value)} ★</Badge>}
              <span>{formatCount(item.plays)} plays</span>
              {item.popularity !== null && <span>Popularity {formatCompact(item.popularity)}</span>}
            </div>
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between gap-3">
        <StarRating
          label={`Rate ${item.title}`}
          value={value}
          size="size-7"
          onChange={(rating) => {
            rate(item.rating_key, rating)
            if (rating !== null) {
              onSwiped(item)
              queue.advance()
            }
          }}
        />
        <Button variant="ghost" onClick={() => queue.advance()}>
          Skip
        </Button>
      </div>
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
              <span className="hidden w-16 text-right text-xs text-muted tabular-nums sm:block" title="Plays">
                {formatCount(item.plays)} ▶
              </span>
              <span className="hidden w-10 text-right text-xs text-muted tabular-nums md:block">{formatDuration(item.duration_ms)}</span>
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
      title={label}
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

