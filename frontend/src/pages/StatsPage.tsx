import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Tooltip } from '../components/Tooltip'
import { errorMessage, type Schemas } from '../api/client'
import { useActiveServer } from '../api/library'
import { localTimeZone, useSkips, useStatsActivity, useStatsOverview, type SkipKind, type TopBy } from '../api/stats'
import type { ListenFilter } from '../api/listens'
import { ListensDialog } from '../components/listens'
import { ColumnChart, Heatmap, type ColumnPoint } from '../components/charts'
import { Artwork } from '../components/library'
import { RangePicker } from '../components/RangePicker'
import { Card, Notice, Spinner } from '../components/ui'
import { formatChange, formatCount, formatListeningTime } from '../format'

const PREVIOUS: Record<string, string> = {
  '7d': 'previous 7 days',
  '30d': 'previous 30 days',
  '365d': 'previous 12 months',
}

function listens(n: number) {
  return `${formatCount(n)} ${n === 1 ? 'listen' : 'listens'}`
}

// Monday-first weekday names in the viewer's language (2026-11-16 is a Monday).
const DAY_NAMES = Array.from({ length: 7 }, (_, i) =>
  new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(new Date(Date.UTC(2026, 10, 16 + i, 12))),
)

function activityPoints(activity: Schemas['StatsActivityOut']): ColumnPoint[] {
  const day = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' })
  const full = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  const month = new Intl.DateTimeFormat(undefined, { month: 'short', year: 'numeric' })
  return activity.points.map((p) => {
    // Bucket starts are calendar dates; read them as local dates, not UTC midnight.
    const [y, m, d] = p.start.split('-').map(Number)
    const date = new Date(y, m - 1, d)
    if (activity.bucket === 'month') return { label: month.format(date), axis: month.format(date), value: p.listens }
    if (activity.bucket === 'week') return { label: `Week of ${full.format(date)}`, axis: day.format(date), value: p.listens }
    return { label: full.format(date), axis: day.format(date), value: p.listens }
  })
}

/** The local time range a chart bucket covers, as ISO strings for the API. */
function bucketRange(start: string, bucket: Schemas['StatsActivityOut']['bucket']): { start: string; end: string } {
  const [y, m, d] = start.split('-').map(Number)
  const from = new Date(y, m - 1, d)
  const to =
    bucket === 'day'
      ? new Date(y, m - 1, d + 1)
      : bucket === 'week'
        ? new Date(y, m - 1, d + 7)
        : new Date(y, m, 1)
  return { start: from.toISOString(), end: to.toISOString() }
}

function Tile({ label, value, note }: { label: string; value: string; note?: string | null }) {
  return (
    <div className="rounded-2xl border border-border bg-surface px-4 py-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="font-display text-2xl font-bold">{value}</dd>
      {note && <dd className="text-xs text-muted">{note}</dd>}
    </div>
  )
}

function RankChange({ rank, previous, compared }: { rank: number; previous: number | null; compared: boolean }) {
  if (!compared) return null
  if (previous === null) return <span className="text-xs text-accent">New</span>
  const delta = previous - rank
  if (delta === 0) return <span className="text-xs text-muted" aria-label="Same rank">–</span>
  return (
    <Tooltip content={`Previously #${previous}`}>
      <span className={`text-xs tabular-nums ${delta > 0 ? 'text-ok' : 'text-danger'}`}>
        {delta > 0 ? '▲' : '▼'} {Math.abs(delta)}
      </span>
    </Tooltip>
  )
}

export function TopList({
  title,
  items,
  kind,
  serverId,
  compared,
  by,
}: {
  title: string
  items: Schemas['TopItemOut'][]
  kind: 'artist' | 'album' | 'track'
  serverId: number | null
  compared: boolean
  by: TopBy
}) {
  return (
    <Card title={title}>
      {items.length === 0 ? (
        <p className="text-sm text-muted">Nothing played in this period.</p>
      ) : (
        <ol className="space-y-2">
          {items.map((item, i) => {
            const to =
              item.rating_key === null
                ? null
                : kind === 'artist'
                  ? `/library/artists/${item.rating_key}`
                  : `/library/albums/${item.rating_key}`
            const name = to ? (
              <Link to={to} className="hover:underline">
                {item.name}
              </Link>
            ) : (
              item.name
            )
            return (
              <li key={`${item.name}-${item.artist}-${i}`} className="flex items-center gap-3">
                <span className="w-5 shrink-0 text-right text-xs text-muted tabular-nums">{i + 1}</span>
                {serverId !== null && (
                  <Artwork
                    serverId={serverId}
                    path={item.thumb}
                    title={item.name}
                    size={96}
                    round={kind === 'artist'}
                    className="size-9 shrink-0"
                  />
                )}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{name}</p>
                  {item.artist && <p className="truncate text-xs text-muted">{item.artist}</p>}
                </div>
                <RankChange rank={i + 1} previous={item.previous_rank} compared={compared} />
                <span className="w-16 shrink-0 text-right text-xs text-muted tabular-nums">
                  {by === 'time' ? formatListeningTime(item.listening_ms) : formatCount(item.listens)}
                </span>
              </li>
            )
          })}
        </ol>
      )}
    </Card>
  )
}

export function StatsPage() {
  const [params, setParams] = useSearchParams()
  const range = params.get('range') ?? '30d'
  const by: TopBy = params.get('by') === 'time' ? 'time' : 'listens'
  const { active } = useActiveServer()
  const serverId = active?.id ?? null
  const overview = useStatsOverview(range, serverId, by)
  const activity = useStatsActivity(range)
  const [drill, setDrill] = useState<{ title: string; filter: ListenFilter } | null>(null)

  if (overview.isPending) return <Spinner />
  if (overview.isError) return <Notice tone="error">{errorMessage(overview.error)}</Notice>
  const o = overview.data

  if (!o.first_listen_at) {
    return (
      <Card>
        <p className="text-sm text-muted">
          No listens yet. Plays from your Plex servers are added every few minutes once a library has
          synced, and you can import your Last.fm or ListenBrainz history on{' '}
          <Link to="/settings/export/history" className="text-accent underline">
            Settings → Export &amp; import → History
          </Link>
          .
        </p>
      </Card>
    )
  }

  const compared = o.previous !== null
  const vs = PREVIOUS[range] ?? (range.startsWith('y') ? `${Number(range.slice(1)) - 1}` : '')
  const change = (current: number, previous: number | undefined) => {
    const c = formatChange(current, previous)
    return c ? `${c} vs ${vs}` : null
  }

  return (
    <div className={`space-y-4 ${overview.isPlaceholderData ? 'opacity-70' : ''}`}>
      <RangePicker range={range} firstListen={o.first_listen_at} onChange={(key) => setParams({ range: key, by }, { replace: true })} />

      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="Listens" value={formatCount(o.totals.listens)} note={change(o.totals.listens, o.previous?.listens)} />
        <Tile
          label="Listening time"
          value={`≈ ${formatListeningTime(o.totals.listening_ms)}`}
          note={change(o.totals.listening_ms, o.previous?.listening_ms)}
        />
        <Tile
          label="Artists"
          value={formatCount(o.totals.artists)}
          note={range === 'all' ? `${formatCount(o.totals.tracks)} different songs` : `${formatCount(o.new_artists)} new to you`}
        />
        <Tile
          label="Listening streak"
          value={`${formatCount(o.current_streak)} ${o.current_streak === 1 ? 'day' : 'days'}`}
          note={`Longest: ${formatCount(o.longest_streak)} days`}
        />
      </dl>

      <Card title="Listens over time">
        {activity.isPending ? (
          <Spinner small />
        ) : activity.isError ? (
          <Notice tone="error">{errorMessage(activity.error)}</Notice>
        ) : (
          <ColumnChart
            points={activityPoints(activity.data)}
            unit={listens}
            caption={`Listens per ${activity.data.bucket}`}
            onSelect={(i) => {
              const point = activity.data.points[i]
              setDrill({
                title: activityPoints(activity.data)[i].label,
                filter: bucketRange(point.start, activity.data.bucket),
              })
            }}
          />
        )}
      </Card>

      <div className="flex justify-end">
        <div className="inline-flex rounded-lg bg-surface-2 p-0.5 text-sm" role="group" aria-label="Rank top lists">
          {(
            [
              ['listens', 'By listens'],
              ['time', 'By time'],
            ] as const
          ).map(([key, text]) => (
            <button
              key={key}
              type="button"
              aria-pressed={by === key}
              onClick={() => setParams({ range, by: key }, { replace: true })}
              className={`rounded-md px-3 py-1 ${by === key ? 'bg-surface font-medium shadow-sm' : 'text-muted hover:text-text'}`}
            >
              {text}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <TopList title="Top artists" items={o.top_artists} kind="artist" serverId={serverId} compared={compared} by={by} />
        <TopList title="Top albums" items={o.top_albums} kind="album" serverId={serverId} compared={compared} by={by} />
        <TopList title="Top songs" items={o.top_tracks} kind="track" serverId={serverId} compared={compared} by={by} />
      </div>

      {serverId !== null && <MostSkipped serverId={serverId} range={range} />}

      <Card title="When you listen">
        {activity.data ? (
          <Heatmap
            grid={activity.data.heatmap}
            dayNames={DAY_NAMES}
            unit={listens}
            caption="Listens by weekday and hour"
            onSelect={(day, hour) =>
              setDrill({
                title: `${DAY_NAMES[day]} ${String(hour).padStart(2, '0')}:00–${String((hour + 1) % 24).padStart(2, '0')}:00`,
                filter: {
                  weekday: day + 1,
                  hour,
                  tz: localTimeZone(),
                  start: o.period.start ?? undefined,
                  end: o.period.end,
                },
              })
            }
          />
        ) : (
          <Spinner small />
        )}
      </Card>
      {drill && <ListensDialog title={drill.title} filter={drill.filter} onClose={() => setDrill(null)} />}
    </div>
  )
}

const SKIP_KINDS: [SkipKind, string][] = [
  ['track', 'Songs'],
  ['artist', 'Artists'],
]

function MostSkipped({ serverId, range }: { serverId: number; range: string }) {
  const [kind, setKind] = useState<SkipKind>('track')
  const skips = useSkips(serverId, range, kind)
  const data = skips.data
  return (
    <Card
      title="Most skipped"
      action={
        <div className="inline-flex rounded-lg bg-surface-2 p-0.5 text-xs" role="group" aria-label="Skipped">
          {SKIP_KINDS.map(([key, text]) => (
            <button
              key={key}
              type="button"
              aria-pressed={kind === key}
              onClick={() => setKind(key)}
              className={`rounded-md px-2.5 py-1 ${kind === key ? 'bg-surface font-medium shadow-sm' : 'text-muted hover:text-text'}`}
            >
              {text}
            </button>
          ))}
        </div>
      }
    >
      {skips.isPending ? (
        <Spinner small />
      ) : skips.isError ? (
        <Notice tone="error">{errorMessage(skips.error)}</Notice>
      ) : !data!.watching ? (
        <p className="text-sm text-muted">
          Skips are recorded from now on: Plekstender checks what's playing in Plex every 10 seconds and notes how far
          each song got.
        </p>
      ) : data!.items.length === 0 ? (
        <p className="text-sm text-muted">Nothing skipped in this period.</p>
      ) : (
        <ol className="space-y-2">
          {data!.items.map((item, i) => (
            <li key={item.rating_key} className="flex items-center gap-3">
              <span className="w-5 shrink-0 text-right text-xs text-muted tabular-nums">{i + 1}</span>
              <Artwork serverId={serverId} path={item.thumb} title={item.name} size={96} round={kind === 'artist'} className="size-9 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{item.name}</p>
                {item.artist && <p className="truncate text-xs text-muted">{item.artist}</p>}
              </div>
              <span
                className="shrink-0 text-right text-xs text-muted tabular-nums"
                title={`${formatCount(item.skips)} of ${formatCount(item.plays)} ${item.plays === 1 ? 'play' : 'plays'} skipped`}
              >
                {formatCount(item.skips)} {item.skips === 1 ? 'skip' : 'skips'}
              </span>
            </li>
          ))}
        </ol>
      )}
      {data && (
        <p className="mt-3 text-xs text-muted">
          A play counts as skipped when less than {data.threshold}% of the song was heard and another song started
          straight after.{' '}
          <Link to="/settings/listening" className="underline">
            Change
          </Link>
        </p>
      )}
    </Card>
  )
}
