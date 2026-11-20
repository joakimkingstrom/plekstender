import { Link } from 'react-router'
import { useGigStats } from '../api/gigs'
import { useActiveServer } from '../api/library'
import { formatCount } from '../format'
import { localDate } from '../live'
import { ColumnChart } from './charts'
import { Card } from './ui'

const MONTHS = Array.from({ length: 12 }, (_, i) => new Date(2000, i, 1))
const SEASONS = [
  ['winter', 'Winter'],
  ['spring', 'Spring'],
  ['summer', 'Summer'],
  ['autumn', 'Autumn'],
] as const
const gigs = (n: number) => `${formatCount(n)} ${n === 1 ? 'gig' : 'gigs'}`

function Tile({ label, value, note }: { label: string; value: string; note?: string | null }) {
  return (
    <div className="rounded-2xl border border-border bg-surface px-4 py-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="font-display text-2xl font-bold">{value}</dd>
      {note && <dd className="text-xs text-muted">{note}</dd>}
    </div>
  )
}

/** Who, where and when over all the gigs someone went to. */
export function GigStats() {
  const stats = useGigStats()
  const { active } = useActiveServer()
  const s = stats.data
  if (!s || s.total === 0) return null
  const busiest = s.years.reduce((a, b) => (b.gigs > a.gigs ? b : a), s.years[0])
  const topSeason = SEASONS.reduce((a, b) => ((s.seasons[b[0]] ?? 0) > (s.seasons[a[0]] ?? 0) ? b : a), SEASONS[0])
  const topMonth = s.months.indexOf(Math.max(...s.months))
  const year = (iso: string) => localDate(iso).getFullYear()
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label="Gigs" value={formatCount(s.total)} note={s.first && s.last ? `${year(s.first)}–${year(s.last)}` : null} />
        <Tile label="Artists seen" value={formatCount(s.artists)} />
        <Tile label="Venues" value={formatCount(s.venues)} />
        <Tile label="Busiest year" value={String(busiest.year)} note={gigs(busiest.gigs)} />
      </dl>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Gigs a year">
          <ColumnChart
            points={s.years.map((y) => ({ label: String(y.year), axis: String(y.year), value: y.gigs }))}
            unit={gigs}
            caption="Gigs a year"
          />
        </Card>
        <Card title="Time of year">
          <ColumnChart
            points={s.months.map((n, i) => ({
              label: MONTHS[i].toLocaleDateString(undefined, { month: 'long' }),
              axis: MONTHS[i].toLocaleDateString(undefined, { month: 'narrow' }),
              value: n,
            }))}
            unit={gigs}
            height={140}
            caption="Gigs in each month"
          />
          <p className="mt-2 text-sm text-muted">
            Most in {MONTHS[topMonth].toLocaleDateString(undefined, { month: 'long' })}; by season{' '}
            {SEASONS.map(([key, label]) => `${label.toLowerCase()} ${s.seasons[key] ?? 0}`).join(', ')} (most in{' '}
            {topSeason[1].toLowerCase()}).
          </p>
        </Card>
        <Card title="Most seen artists">
          <ol className="space-y-1.5 text-sm">
            {s.top_artists.map((a, i) => (
              <li key={a.name} className="flex items-center gap-3">
                <span className="w-5 shrink-0 text-right text-xs text-muted tabular-nums">{i + 1}</span>
                <span className="min-w-0 flex-1 truncate">
                  {a.rating_key != null && a.server_id === active?.id ? (
                    <Link to={`/library/artists/${a.rating_key}`} className="hover:underline">
                      {a.name}
                    </Link>
                  ) : (
                    a.name
                  )}
                </span>
                <span className="text-muted tabular-nums">{gigs(a.gigs)}</span>
              </li>
            ))}
          </ol>
        </Card>
        <Card title="Most visited venues">
          <ol className="space-y-1.5 text-sm">
            {s.top_venues.map((v, i) => (
              <li key={`${v.name}-${v.city}`} className="flex items-center gap-3">
                <span className="w-5 shrink-0 text-right text-xs text-muted tabular-nums">{i + 1}</span>
                <span className="min-w-0 flex-1 truncate">
                  {v.name}
                  {v.city && <span className="text-muted"> · {v.city}</span>}
                </span>
                <span className="text-muted tabular-nums">{gigs(v.gigs)}</span>
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  )
}
