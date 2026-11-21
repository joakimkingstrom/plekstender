import { Link, useSearchParams } from 'react-router'
import { errorMessage } from '../api/client'
import { useActiveServer } from '../api/library'
import { useListenSummary } from '../api/listens'
import { usePlaceDetail, usePlaces, type MapArea } from '../api/stats'
import { ListensMap } from '../components/ListensMap'
import { RangePicker } from '../components/RangePicker'
import { Card, Notice, Spinner } from '../components/ui'
import { formatCount, formatListeningTime } from '../format'
import { CONTINENTS, countryName } from '../places'
import { TopList } from './StatsPage'

/** The area in the URL: ?continent=EU or ?country=NO (with its continent). */
function areaFrom(params: URLSearchParams): MapArea {
  const country = params.get('country')
  const continent = params.get('continent')
  if (country && /^[A-Z]{2}$/.test(country)) return { level: 'country', continent, country }
  if (continent && continent in CONTINENTS) return { level: 'continent', continent }
  return { level: 'world' }
}

function areaName(area: MapArea): string {
  if (area.level === 'country') return countryName(area.country)
  if (area.level === 'continent') return CONTINENTS[area.continent] ?? area.continent
  return 'Everywhere'
}

export function MapPage() {
  const [params, setParams] = useSearchParams()
  const range = params.get('range') ?? '30d'
  const area = areaFrom(params)
  const summary = useListenSummary()
  const places = usePlaces(range)
  const { active } = useActiveServer()

  const go = (next: MapArea, nextRange = range) => {
    const query: Record<string, string> = { range: nextRange }
    if (next.level === 'continent') query.continent = next.continent
    if (next.level === 'country') {
      query.country = next.country
      if (next.continent) query.continent = next.continent
    }
    setParams(query, { replace: true })
  }

  if (places.isPending) return <Spinner />
  if (places.isError) return <Notice tone="error">{errorMessage(places.error)}</Notice>
  const data = places.data
  const inArea = data.countries.filter((c) =>
    area.level === 'world' ? true : area.level === 'continent' ? c.continent_code === area.continent : c.country_code === area.country,
  )

  return (
    <div className={`space-y-4 ${places.isPlaceholderData ? 'opacity-70' : ''}`}>
      <RangePicker range={range} firstListen={summary.data?.first_at} onChange={(r) => go(area, r)} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <div className="min-w-0 space-y-4">
          <Card>
            <nav className="mb-3 flex flex-wrap items-center gap-1 text-sm" aria-label="Map area">
              <AreaLink current={area.level === 'world'} onClick={() => go({ level: 'world' })}>
                World
              </AreaLink>
              {area.level !== 'world' && area.continent && (
                <>
                  <span className="text-muted" aria-hidden>›</span>
                  <AreaLink
                    current={area.level === 'continent'}
                    onClick={() => go({ level: 'continent', continent: area.continent! })}
                  >
                    {CONTINENTS[area.continent!] ?? area.continent}
                  </AreaLink>
                </>
              )}
              {area.level === 'country' && (
                <>
                  <span className="text-muted" aria-hidden>›</span>
                  <AreaLink current onClick={() => undefined}>
                    {countryName(area.country)}
                  </AreaLink>
                </>
              )}
            </nav>
            <ListensMap countries={data.countries} area={area} onArea={(next) => go(next)} />
            <p className="mt-3 text-xs text-muted">
              {data.total === 0
                ? 'No listens in this period.'
                : `${formatCount(data.located)} of ${formatCount(data.total)} listens in this period have a place. `}
              Only plays seen live while they played have one (imported scrobbles don't); plays at home count where
              your server is. Click a country to zoom in.{' '}
              <a href="https://db-ip.com" target="_blank" rel="noreferrer" className="underline hover:text-text">
                {data.attribution}
              </a>
              .
            </p>
          </Card>
          {inArea.length > 0 && area.level !== 'country' && (
            <Card title={area.level === 'world' ? 'Countries' : `Countries in ${areaName(area)}`}>
              <ol className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
                {inArea.map((c, i) => (
                  <li key={c.country_code}>
                    <button
                      type="button"
                      onClick={() => go({ level: 'country', continent: c.continent_code, country: c.country_code })}
                      className="flex w-full items-center gap-3 rounded-md px-1 py-1 text-left text-sm hover:bg-surface-2"
                    >
                      <span className="w-5 text-right text-xs tabular-nums text-muted">{i + 1}</span>
                      <span className="min-w-0 flex-1 truncate">{countryName(c.country_code)}</span>
                      <span className="text-xs tabular-nums text-muted">{formatCount(c.listens)}</span>
                    </button>
                  </li>
                ))}
              </ol>
            </Card>
          )}
        </div>
        <AreaDetail range={range} area={area} serverId={active?.id ?? null} />
      </div>
    </div>
  )
}

function AreaLink({ current, onClick, children }: { current: boolean; onClick: () => void; children: string }) {
  return current ? (
    <span aria-current="page" className="rounded-md px-1.5 py-0.5 font-semibold">
      {children}
    </span>
  ) : (
    <button type="button" onClick={onClick} className="rounded-md px-1.5 py-0.5 text-accent hover:bg-surface-2">
      {children}
    </button>
  )
}

function AreaDetail({ range, area, serverId }: { range: string; area: MapArea; serverId: number | null }) {
  const detail = usePlaceDetail(range, area, serverId)
  if (detail.isPending) return <Spinner />
  if (detail.isError) return <Notice tone="error">{errorMessage(detail.error)}</Notice>
  const d = detail.data
  return (
    <div className={`min-w-0 space-y-4 ${detail.isPlaceholderData ? 'opacity-70' : ''}`}>
      <Card>
        <h2 className="font-display text-xl font-bold">{areaName(area)}</h2>
        <p className="text-sm text-muted">
          {formatCount(d.listens)} {d.listens === 1 ? 'listen' : 'listens'} · {formatListeningTime(d.listening_ms)}
        </p>
        {d.cities.length > 0 && (
          <ul className="mt-3 space-y-1 text-sm">
            {d.cities.map((c) => (
              <li key={c.name} className="flex justify-between gap-3">
                <span className="truncate">{c.name === 'Local network' ? 'At home (local network)' : c.name}</span>
                <span className="text-xs tabular-nums text-muted">{formatCount(c.listens)}</span>
              </li>
            ))}
          </ul>
        )}
        {d.listens === 0 && (
          <p className="mt-2 text-sm text-muted">
            Nothing played here in this period. See{' '}
            <Link to="/history" className="text-accent underline">
              Overview
            </Link>{' '}
            for everything.
          </p>
        )}
      </Card>
      {d.listens > 0 && (
        <>
          <TopList title="Top artists" items={d.top_artists} kind="artist" serverId={serverId} compared={false} by="listens" />
          <TopList title="Top songs" items={d.top_tracks} kind="track" serverId={serverId} compared={false} by="listens" />
        </>
      )}
    </div>
  )
}
