import { useQuery } from '@tanstack/react-query'
import { useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { Link } from 'react-router'
import { api, unwrap, type Schemas } from '../api/client'
import { useLiveSettings, useLiveShows } from '../api/live'
import { useRated } from '../api/ratings'
import { useOnThisDay, useStatsActivity, useStatsOverview } from '../api/stats'
import { formatChange, formatCount, formatListeningTime } from '../format'
import { ColumnChart } from './charts'
import { GigRow } from './GigRow'
import { Artwork, Stars } from './library'
import { Segmented } from './Segmented'
import { Card, Spinner } from './ui'

const more = 'text-sm text-accent hover:underline'

function Tile({ label, value, note }: { label: string; value: string; note?: string | null }) {
  return (
    <div className="rounded-xl bg-surface-2/60 p-3">
      <p className="text-xs font-semibold uppercase tracking-wider text-muted">{label}</p>
      <p className="font-display text-2xl font-bold tabular-nums">{value}</p>
      {note && <p className="text-xs text-muted">{note}</p>}
    </div>
  )
}

/** This week and month at a glance, with the last 30 days' listens day by day. */
export function YourListening({ serverId }: { serverId: number | null }) {
  const week = useStatsOverview('7d', serverId)
  const month = useStatsOverview('30d', serverId)
  const activity = useStatsActivity('30d')
  if (week.isPending || month.isPending) return <Card title="Your listening"><Spinner /></Card>
  const w = week.data
  const m = month.data
  if (!w?.totals || !m?.totals) return null
  if (m.totals.listens === 0 && !m.first_listen_at)
    return (
      <Card title="Your listening">
        <p className="text-sm text-muted">
          No listens yet. Plays in Plex show up here, and you can import your Last.fm or ListenBrainz history on{' '}
          <Link to="/settings/export/history" className="text-accent hover:underline">
            Settings → Export &amp; import → History
          </Link>
          .
        </p>
      </Card>
    )
  const change = (now: number, before: number | undefined) => {
    const c = formatChange(now, before)
    return c ? `${c} on the week before` : null
  }
  const day = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' })
  const full = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
  const points = (activity.data?.points ?? []).map((p) => {
    const [y, mo, d] = p.start.split('-').map(Number)
    const date = new Date(y, mo - 1, d)
    return { label: full.format(date), axis: day.format(date), value: p.listens }
  })
  return (
    <Card
      title="Your listening"
      action={
        <span className="flex gap-3">
          <Link to="/history/rewind" className={more}>
            Rewind
          </Link>
          <Link to="/history" className={more}>
            History
          </Link>
        </span>
      }
    >
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label="This week" value={formatCount(w.totals.listens)} note={change(w.totals.listens, w.previous?.listens)} />
        <Tile label="Last 30 days" value={formatCount(m.totals.listens)} note={`${formatCount(m.totals.artists)} artists`} />
        <Tile label="Time listening" value={formatListeningTime(m.totals.listening_ms)} note="last 30 days" />
        <Tile
          label="Streak"
          value={`${formatCount(m.current_streak)} ${m.current_streak === 1 ? 'day' : 'days'}`}
          note={`Longest ${formatCount(m.longest_streak)}`}
        />
      </div>
      {points.length > 0 && (
        <div className="mt-4">
          <ColumnChart points={points} unit={(n) => `${formatCount(n)} ${n === 1 ? 'listen' : 'listens'}`} caption="Listens per day, the last 30 days" height={140} />
        </div>
      )}
    </Card>
  )
}

// Artist rows are one line, album rows two: six artists fill the card beside five albums.
const TOP_ROWS = { artist: 6, album: 5 } as const

function TopList({ serverId, items, kind }: { serverId: number | null; items: Schemas['TopItemOut'][]; kind: 'artist' | 'album' }) {
  const max = Math.max(1, ...items.map((i) => i.listens))
  return (
    <ol className="space-y-2">
      {items.slice(0, TOP_ROWS[kind]).map((item, i) => {
        const to = item.rating_key ? `/library/${kind === 'artist' ? 'artists' : 'albums'}/${item.rating_key}` : null
        const body = (
          <>
            <span className="w-4 shrink-0 text-right text-xs text-muted tabular-nums">{i + 1}</span>
            {serverId !== null && (
              <Artwork serverId={serverId} path={item.thumb} title={item.name} size={64} round={kind === 'artist'} className="size-9 shrink-0" />
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{item.name}</span>
              {item.artist && <span className="block truncate text-xs text-muted">{item.artist}</span>}
              <span className="mt-1 block h-1 rounded-full bg-chart" style={{ width: `${(item.listens / max) * 100}%` }} aria-hidden />
            </span>
            <span className="shrink-0 text-xs text-muted tabular-nums">{formatCount(item.listens)}</span>
          </>
        )
        const label = `${item.name}${item.artist ? ` by ${item.artist}` : ''}: ${formatCount(item.listens)} ${item.listens === 1 ? 'listen' : 'listens'}`
        return (
          <li key={`${item.name}-${item.artist}`}>
            {to ? (
              <Link to={to} aria-label={label} className="flex items-center gap-2 rounded-lg hover:bg-surface-2/60">
                {body}
              </Link>
            ) : (
              <div className="flex items-center gap-2">{body}</div>
            )}
          </li>
        )
      })}
    </ol>
  )
}

/** The month's top artists and albums. */
export function TopThisMonth({ serverId }: { serverId: number | null }) {
  const month = useStatsOverview('30d', serverId)
  const m = month.data
  if (!m?.top_artists?.length) return null
  return (
    <>
      <Card title="Top artists · 30 days">
        <TopList serverId={serverId} items={m.top_artists} kind="artist" />
      </Card>
      <Card title="Top albums · 30 days">
        <TopList serverId={serverId} items={m.top_albums} kind="album" />
      </Card>
    </>
  )
}

/** Albums or artists most recently added to Plex. */
export function RecentlyAdded({ serverId }: { serverId: number }) {
  const [kind, setKind] = useState<'album' | 'artist'>('album')
  const recent = useQuery({
    queryKey: ['servers', serverId, 'library', 'recent', kind],
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/recent', {
          params: { path: { server_id: serverId }, query: { kind, limit: 12 } },
        }),
      ),
  })
  return (
    <Card
      title="Recently added"
      action={
        <Segmented
          label="Recently added"
          value={kind}
          onChange={setKind}
          options={[
            ['album', 'Albums'],
            ['artist', 'Artists'],
          ]}
        />
      }
      className="md:col-span-2"
    >
      {recent.isPending ? (
        <Spinner />
      ) : !Array.isArray(recent.data) || recent.data.length === 0 ? (
        <p className="text-sm text-muted">Nothing yet.</p>
      ) : (
        <ul className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6">
          {recent.data.map((item) => (
            <li key={item.rating_key}>
              <Link to={`/library/${kind === 'album' ? 'albums' : 'artists'}/${item.rating_key}`} className="group block">
                <Artwork serverId={serverId} path={item.thumb} title={item.title} round={kind === 'artist'} className="w-full group-hover:opacity-85" />
                <div className={`mt-1 truncate text-xs font-medium ${kind === 'artist' ? 'text-center' : ''}`}>{item.title}</div>
              </Link>
              {item.subtitle && <div className="truncate text-xs text-muted">{item.subtitle}</div>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/** The next few gigs from the Gigs page (when a place is set). */
/** The gigs you've got tickets for; when there are none, the gigs coming up near you. */
export function UpcomingGigs({ serverId }: { serverId: number | null }) {
  const settings = useLiveSettings()
  const placed = Boolean(settings.data?.place_name)
  const starred = useLiveShows({ starred: true }, false)
  const shows = useLiveShows({}, false)
  if (settings.isPending || !settings.data || starred.isPending) return null
  const ticketed = (starred.data?.shows ?? []).filter((show) => show.got_ticket)
  const allGigs = (
    <Link to="/gigs" className={more}>
      All gigs
    </Link>
  )
  if (ticketed.length)
    return (
      <Card title="Gigs you've got tickets for" action={allGigs}>
        <ul className="-mx-3 divide-y divide-border">
          {ticketed.slice(0, 4).map((show) => (
            <GigRow key={show.id} show={show} serverId={serverId} ticketPill={false} />
          ))}
        </ul>
        {ticketed.length > 4 && (
          <p className="mt-2 text-sm text-muted">
            And {ticketed.length - 4} more on{' '}
            <Link to="/gigs" className="text-accent hover:underline">
              Gigs
            </Link>
            .
          </p>
        )}
      </Card>
    )
  if (!placed)
    return (
      <Card title="Gigs coming up">
        <p className="text-sm text-muted">
          Set your place on{' '}
          <Link to="/gigs" className="text-accent hover:underline">
            Gigs
          </Link>{' '}
          to see gigs near you by the artists you listen to.
        </p>
      </Card>
    )
  const list = shows.data?.shows ?? []
  return (
    <Card title="Gigs coming up" action={allGigs}>
      {shows.isPending ? (
        <Spinner />
      ) : list.length === 0 ? (
        <p className="text-sm text-muted">No gigs found near you yet.</p>
      ) : (
        <ul className="-mx-3 divide-y divide-border">
          {list.slice(0, 4).map((show) => (
            <GigRow key={show.id} show={show} serverId={serverId} />
          ))}
        </ul>
      )}
    </Card>
  )
}

/** What you rated last. */
/** At least this many latest ratings; more when the card is taller (next to a longer one). */
const MIN_RATINGS = 6
const RATING_GAP = 8 // px between rows (space-y-2)
const RATING_ROW = 44 // px a row takes before it's measured (a 36 px cover and two lines)

/** How many rows fit in `area` (at least MIN_RATINGS), measured as it changes size. */
function useRowsThatFit(area: RefObject<HTMLDivElement | null>, list: RefObject<HTMLUListElement | null>, ready: boolean) {
  const [rows, setRows] = useState(MIN_RATINGS)
  const [rowHeight, setRowHeight] = useState(RATING_ROW)
  useLayoutEffect(() => {
    const box = area.current
    if (!box || !ready) return
    const measure = () => {
      const first = list.current?.firstElementChild as HTMLElement | null | undefined
      const height = first?.offsetHeight || RATING_ROW
      setRowHeight(height)
      setRows(Math.max(MIN_RATINGS, Math.floor((box.clientHeight + RATING_GAP) / (height + RATING_GAP))))
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(box)
    return () => observer?.disconnect()
  }, [area, list, ready])
  return { rows, minHeight: MIN_RATINGS * (rowHeight + RATING_GAP) - RATING_GAP }
}

export function LatestRatings({ serverId }: { serverId: number }) {
  const rated = useRated(serverId, null)
  const items = rated.data?.pages?.[0]?.items ?? []
  const area = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLUListElement>(null)
  // As many as fit: the card is as tall as the one beside it; the list takes no room of its
  // own beyond six rows (it's laid over its area), so it can't make the row taller itself.
  const { rows, minHeight } = useRowsThatFit(area, list, items.length > 0)
  if (rated.isPending || items.length === 0) return null
  return (
    <Card
      className="flex flex-col"
      title="Latest ratings"
      action={
        <Link to="/history/ratings" className={more}>
          All ratings
        </Link>
      }
    >
      <div ref={area} className="relative flex-1" style={{ minHeight }}>
        <ul ref={list} className="absolute inset-x-0 top-0 space-y-2">
          {items.slice(0, rows).map((item) => {
            const to = item.link_key ? `/library/${item.kind === 'artist' ? 'artists' : 'albums'}/${item.link_key}` : null
            return (
              <li key={`${item.kind}-${item.rating_key}`} className="flex items-center gap-2">
                <Artwork serverId={serverId} path={item.thumb} title={item.title} size={64} round={item.kind === 'artist'} className="size-9 shrink-0" />
                <span className="min-w-0 flex-1">
                  {to ? (
                    <Link to={to} className="block truncate text-sm font-medium hover:underline">
                      {item.title}
                    </Link>
                  ) : (
                    <span className="block truncate text-sm font-medium">{item.title}</span>
                  )}
                  {item.subtitle && <span className="block truncate text-xs text-muted">{item.subtitle}</span>}
                </span>
                <Stars rating={item.user_rating} className="shrink-0" />
              </li>
            )
          })}
        </ul>
      </div>
    </Card>
  )
}

function yearsAgo(year: number, now: number) {
  const n = now - year
  return n === 1 ? 'A year ago' : `${n} years ago`
}

/** What you played on today's date in earlier years (the last few years with listens). */
function TicketIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-4 shrink-0 text-accent" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-label="Gig" role="img">
      <path d="M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-2a2 2 0 0 0 0-4z" />
      <path d="M14 6v12" strokeDasharray="2 2" />
    </svg>
  )
}

export function OnThisDay({ serverId }: { serverId: number | null }) {
  const day = useOnThisDay(serverId)
  const years = day.data?.years.slice(0, 4) ?? []
  if (years.length === 0) return null
  const now = Number(day.data!.date.slice(0, 4))
  return (
    <Card title="On this day" className="md:col-span-2">
      <ul className="grid gap-4 sm:grid-cols-2">
        {years.map((y) => {
          const [top] = y.tracks
          return (
            <li key={y.year} className="flex gap-3">
              {serverId !== null && y.album_thumb ? (
                <Link to={`/library/albums/${y.album_rating_key}`} className="shrink-0" title={y.album ?? undefined}>
                  <Artwork serverId={serverId} path={y.album_thumb} title={y.album ?? ''} size={96} className="size-12" />
                </Link>
              ) : (
                <span className="flex size-12 shrink-0 items-center justify-center rounded-md bg-surface-2 font-display text-sm font-bold text-muted">
                  {y.year}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <p className="text-sm">
                  <span className="font-semibold">{yearsAgo(y.year, now)}</span>{' '}
                  <span className="text-muted">
                    · {y.year}
                    {y.listens > 0 && ` · ${formatCount(y.listens)} ${y.listens === 1 ? 'listen' : 'listens'}`}
                  </span>
                </p>
                {(y.gigs ?? []).map((g) => (
                  <p key={g.id} className="flex items-center gap-1.5 truncate text-sm">
                    <TicketIcon />
                    <span className="truncate">
                      {[g.artists.join(', '), [g.venue, g.city].filter(Boolean).join(', ')].filter(Boolean).join(' · ') || 'A gig'}
                    </span>
                    {g.setlist_url && (
                      <a
                        href={g.setlist_url}
                        target="_blank"
                        rel="noreferrer"
                        className="shrink-0 text-xs text-accent hover:underline"
                        aria-label={`The setlist on setlist.fm${g.artists[0] ? ` (${g.artists[0]})` : ''}`}
                      >
                        Setlist
                      </a>
                    )}
                  </p>
                ))}
                <p className="truncate text-sm">
                  {y.artists.map((a, i) => (
                    <span key={a.name}>
                      {i > 0 && ', '}
                      {a.rating_key !== null ? (
                        <Link to={`/library/artists/${a.rating_key}`} className="hover:underline">
                          {a.name}
                        </Link>
                      ) : (
                        a.name
                      )}
                    </span>
                  ))}
                </p>
                {top && (
                  <p className="truncate text-xs text-muted">
                    Most played: {top.name} by {top.artist}
                    {top.listens > 1 ? ` (${top.listens}×)` : ''}
                  </p>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}
