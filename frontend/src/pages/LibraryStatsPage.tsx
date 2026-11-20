import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useLibraryStats, useReleased, useWords, type WordKind } from '../api/libraryStats'
import { AreaChart, ColumnChart } from '../components/charts'
import { Artwork } from '../components/library'
import { Segmented } from '../components/Segmented'
import { Card, Notice, Spinner } from '../components/ui'
import { useLibraryServer } from '../components/useLibraryServer'
import { formatCount } from '../format'

type Measure = 'tracks' | 'albums' | 'artists'

const MEASURES: [Measure, string][] = [
  ['tracks', 'Tracks'],
  ['albums', 'Albums'],
  ['artists', 'Artists'],
]

const monthName = (month: number, style: 'short' | 'long' = 'short') =>
  new Date(2000, month - 1, 1).toLocaleString(undefined, { month: style })

function plural(n: number, one: string, many: string) {
  return `${formatCount(n)} ${n === 1 ? one : many}`
}

/** Library → Stats. */
export function LibraryStatsPage() {
  const { active, blocker } = useLibraryServer()
  if (blocker) return blocker
  return <Stats serverId={active!.id} />
}

function Stats({ serverId }: { serverId: number }) {
  const stats = useLibraryStats(serverId)
  if (stats.isPending) return <Spinner />
  if (stats.isError) return <Notice tone="error">{errorMessage(stats.error)}</Notice>
  const s = stats.data
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-3 gap-3">
        {(
          [
            ['Artists', s.artists],
            ['Albums', s.albums],
            ['Tracks', s.tracks],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="rounded-xl border border-border bg-surface p-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted">{label}</p>
            <p className="font-display text-2xl font-bold tabular-nums sm:text-3xl">{formatCount(value)}</p>
          </div>
        ))}
      </div>
      <Growth growth={s.growth} />
      <Decades serverId={serverId} decades={s.decades} undated={s.undated_albums} />
      <Words serverId={serverId} />
    </div>
  )
}

function Growth({ growth }: { growth: Schemas['GrowthPoint'][] }) {
  const [measure, setMeasure] = useState<Measure>('tracks')
  const label = MEASURES.find(([m]) => m === measure)![1].toLowerCase()
  const points = growth.map((g) => {
    const [year, month] = g.month.split('-').map(Number)
    return { label: `${monthName(month, 'long')} ${year}`, axis: String(year), value: g[measure] }
  })
  return (
    <Card title="The library over time" action={<Segmented label="Count" value={measure} options={MEASURES} onChange={setMeasure} />}>
      {points.length < 2 ? (
        <p className="text-sm text-muted">Everything was added to Plex in the same month.</p>
      ) : (
        <AreaChart points={points} unit={(n) => `${formatCount(n)} ${label}`} caption={`Total ${label} by when they were added to Plex`} />
      )}
      <p className="mt-2 text-xs text-muted">By when they were added to Plex, month by month.</p>
    </Card>
  )
}

function Decades({ serverId, decades, undated }: { serverId: number; decades: Schemas['DecadeCount'][]; undated: number }) {
  const [decade, setDecade] = useState<number | null>(null)
  const [year, setYear] = useState<number | null>(null)
  const released = useReleased(serverId, year !== null ? { year } : decade !== null ? { decade } : null)
  const albums = (n: number) => plural(n, 'album', 'albums')

  let chart
  if (decade === null) {
    chart = (
      <ColumnChart
        points={decades.map((d) => ({ label: `The ${d.decade}s`, axis: `${d.decade}s`, value: d.albums }))}
        unit={albums}
        caption="Albums by the decade they were released"
        onSelect={(i) => setDecade(decades[i].decade)}
      />
    )
  } else if (released.isPending) {
    chart = <Spinner />
  } else if (released.isError) {
    chart = <Notice tone="error">{errorMessage(released.error)}</Notice>
  } else if (year === null) {
    const buckets = released.data.buckets
    chart = (
      <ColumnChart
        points={buckets.map((b) => ({ label: String(b.decade), axis: `’${String(b.decade).slice(2)}`, value: b.albums }))}
        unit={albums}
        caption={`Albums released in the ${decade}s, by year`}
        onSelect={(i) => buckets[i].albums > 0 && setYear(buckets[i].decade)}
      />
    )
  } else {
    // Month 0 holds albums with only a year; shown last, and only when there are any.
    const buckets = [...released.data.buckets.slice(1), released.data.buckets[0]].filter((b) => b.decade > 0 || b.albums > 0)
    chart = (
      <>
        <ColumnChart
          points={buckets.map((b) => ({
            label: b.decade ? `${monthName(b.decade, 'long')} ${year}` : `${year}, month unknown`,
            axis: b.decade ? monthName(b.decade) : '?',
            value: b.albums,
          }))}
          unit={albums}
          caption={`Albums released in ${year}, by month`}
        />
        <ul className="mt-4 grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6">
          {released.data.albums.map((a) => (
            <li key={a.rating_key}>
              <Link to={`/library/albums/${a.rating_key}`} className="group block">
                <Artwork serverId={serverId} path={a.thumb} title={a.title} className="w-full group-hover:opacity-85" />
                <div className="mt-1 truncate text-xs font-medium">{a.title}</div>
              </Link>
              <div className="truncate text-xs text-muted">{a.artist_title}</div>
            </li>
          ))}
        </ul>
      </>
    )
  }

  return (
    <Card title="Music by decade">
      <nav aria-label="Decade, year" className="mb-3 flex flex-wrap items-center gap-1 text-sm">
        <button type="button" onClick={() => (setDecade(null), setYear(null))} className={decade === null ? 'font-semibold' : 'text-accent hover:underline'}>
          Every decade
        </button>
        {decade !== null && (
          <>
            <span className="text-muted">›</span>
            <button type="button" onClick={() => setYear(null)} className={year === null ? 'font-semibold' : 'text-accent hover:underline'}>
              The {decade}s
            </button>
          </>
        )}
        {year !== null && (
          <>
            <span className="text-muted">›</span>
            <span className="font-semibold">{year}</span>
          </>
        )}
      </nav>
      {chart}
      <p className="mt-2 text-xs text-muted">
        {decade === null
          ? `By release year; click a decade for its years, then a year for its months.${undated ? ` ${albums(undated)} without a year ${undated === 1 ? "isn't" : "aren't"} counted.` : ''}`
          : year === null
            ? 'Click a year for its months and albums.'
            : 'Albums with only a year count as "?".'}
      </p>
    </Card>
  )
}

const WORD_KINDS: [WordKind, string][] = [
  ['track', 'Songs'],
  ['album', 'Albums'],
  ['artist', 'Artists'],
]

/** A stable shuffle (by the word), so the biggest words don't all sit at the start. */
function scatter<T extends { word: string }>(items: T[]): T[] {
  const hash = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7)
  return [...items].sort((a, b) => hash(a.word) - hash(b.word))
}

function Words({ serverId }: { serverId: number }) {
  const [kind, setKind] = useState<WordKind>('track')
  const words = useWords(serverId, kind)
  const shown = useMemo(() => scatter(words.data ?? []), [words.data])
  const max = Math.max(1, ...(words.data ?? []).map((w) => w.count))
  const min = Math.min(max, ...(words.data ?? []).map((w) => w.count))
  const what = WORD_KINDS.find(([k]) => k === kind)![1].toLowerCase()
  return (
    <Card title="Words in names" action={<Segmented label="Names of" value={kind} options={WORD_KINDS} onChange={setKind} />}>
      {words.isPending ? (
        <Spinner />
      ) : words.isError ? (
        <Notice tone="error">{errorMessage(words.error)}</Notice>
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted">No words yet.</p>
      ) : (
        <ul className="flex flex-wrap items-baseline justify-center gap-x-3 gap-y-1 py-2" aria-label={`The most common words in ${what}' names`}>
          {shown.map((w) => {
            // Square-root scale between 0.8rem and 2.8rem.
            const t = max === min ? 1 : Math.sqrt((w.count - min) / (max - min))
            return (
              <li
                key={w.word}
                title={`${w.word}: in ${plural(w.count, 'name', 'names')}`}
                className={t > 0.45 ? 'font-display font-bold text-text' : t > 0.15 ? 'font-semibold text-text' : 'text-muted'}
                style={{ fontSize: `${0.8 + t * 2}rem`, lineHeight: 1.15 }}
              >
                {w.word}
                <span className="sr-only">: {formatCount(w.count)}</span>
              </li>
            )
          })}
        </ul>
      )}
      <p className="mt-2 text-xs text-muted">
        The 100 most common words in {what}' names, sized by how many names they're in. Words like "the", "and" or
        "remastered" are left out (in several languages).
      </p>
    </Card>
  )
}
