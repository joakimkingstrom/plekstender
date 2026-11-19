import { Link, useSearchParams } from 'react-router'
import { Tooltip } from '../components/Tooltip'
import { errorMessage, type Schemas } from '../api/client'
import { useActiveServer } from '../api/library'
import { useStatsActivity, useStatsOverview } from '../api/stats'
import { ColumnChart, Heatmap, type ColumnPoint } from '../components/charts'
import { Artwork } from '../components/library'
import { Card, Notice, Spinner } from '../components/ui'
import { formatChange, formatCount, formatListeningTime } from '../format'

const FIXED_RANGES: [string, string][] = [
  ['7d', '7 days'],
  ['30d', '30 days'],
  ['365d', '12 months'],
]

const PREVIOUS: Record<string, string> = {
  '7d': 'previous 7 days',
  '30d': 'previous 30 days',
  '365d': 'previous 12 months',
}

function rangeOptions(firstListen: string | null | undefined): [string, string][] {
  const thisYear = new Date().getFullYear()
  const firstYear = firstListen ? new Date(firstListen).getFullYear() : thisYear
  const years: [string, string][] = []
  for (let y = thisYear; y >= firstYear; y--) years.push([`y${y}`, String(y)])
  return [['all', 'All time'], ...FIXED_RANGES, ...years]
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

function TopList({
  title,
  items,
  kind,
  serverId,
  compared,
}: {
  title: string
  items: Schemas['TopItemOut'][]
  kind: 'artist' | 'album' | 'track'
  serverId: number | null
  compared: boolean
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
                <span className="w-12 shrink-0 text-right text-xs text-muted tabular-nums">{formatCount(item.listens)}</span>
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
  const { active } = useActiveServer()
  const serverId = active?.id ?? null
  const overview = useStatsOverview(range, serverId)
  const activity = useStatsActivity(range)

  if (overview.isPending) return <Spinner />
  if (overview.isError) return <Notice tone="error">{errorMessage(overview.error)}</Notice>
  const o = overview.data

  if (!o.first_listen_at) {
    return (
      <Card>
        <p className="text-sm text-muted">
          No listens yet. Plays from your Plex servers are added every few minutes once a library has
          synced, and you can import your Last.fm history on{' '}
          <Link to="/settings/listening" className="text-accent underline">
            Settings → Listening
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
      <div className="no-scrollbar -mx-4 overflow-x-auto px-4">
        <div className="flex w-max gap-1.5" role="group" aria-label="Period">
          {rangeOptions(o.first_listen_at).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={key === range}
              onClick={() => setParams({ range: key }, { replace: true })}
              className={`rounded-full px-3 py-1.5 text-sm font-medium ${
                key === range ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted hover:text-text'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

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
          />
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <TopList title="Top artists" items={o.top_artists} kind="artist" serverId={serverId} compared={compared} />
        <TopList title="Top albums" items={o.top_albums} kind="album" serverId={serverId} compared={compared} />
        <TopList title="Top songs" items={o.top_tracks} kind="track" serverId={serverId} compared={compared} />
      </div>

      <Card title="When you listen">
        {activity.data ? (
          <Heatmap grid={activity.data.heatmap} dayNames={DAY_NAMES} unit={listens} caption="Listens by weekday and hour" />
        ) : (
          <Spinner small />
        )}
      </Card>
    </div>
  )
}
