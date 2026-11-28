import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useSavePlaylist } from '../api/playlists'
import { Dialog } from '../components/Dialog'
import { errorMessage, type Schemas } from '../api/client'
import {
  useArtistCountries,
  useColourFamily,
  useColours,
  type ColourName,
  useCountryArtists,
  useLibraryStats,
  useReleased,
  useFileTypes,
  useSeenLive,
  useWords,
  type WordKind,
} from '../api/libraryStats'
import type { MapArea } from '../api/stats'
import { ListensMap } from '../components/ListensMap'
import { CONTINENTS, countryName } from '../places'
import { AreaChart, ColumnChart, Donut, type DonutSlice } from '../components/charts'
import { Artwork } from '../components/library'
import { Segmented } from '../components/Segmented'
import { WordCloud } from '../components/WordCloud'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { useLibraryServer } from '../components/useLibraryServer'
import { formatBytes, formatCount, formatShare } from '../format'

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
      <SeenLive serverId={serverId} />
      <FileTypes serverId={serverId} />
      <Countries serverId={serverId} />
      <Growth growth={s.growth} />
      <Decades serverId={serverId} decades={s.decades} undated={s.undated_albums} />
      <Colours serverId={serverId} />
      <Words serverId={serverId} />
    </div>
  )
}

const SLOTS = ['var(--color-series-1)', 'var(--color-series-2)', 'var(--color-series-3)', 'var(--color-series-4)']

function songs(n: number) {
  return `${formatCount(n)} ${n === 1 ? 'song' : 'songs'}`
}

/** The library's songs by type of file: the four most common each their own colour, the
 * rest together as "Other" (named in the legend), with how much room they take. */
function FileTypes({ serverId }: { serverId: number }) {
  const types = useFileTypes(serverId)
  if (!types.data?.length) return null
  const name = (format: string) => (format ? format.toUpperCase() : 'Unknown')
  const named = types.data.filter((t) => t.format).slice(0, SLOTS.length)
  const rest = types.data.filter((t) => !named.includes(t))
  const sized = types.data.some((t) => t.sized > 0)
  const size = (bytes: number) => (sized ? formatBytes(bytes) : undefined)
  const slices: DonutSlice[] = named.map((t, i) => ({
    key: t.format,
    label: name(t.format),
    value: t.tracks,
    color: SLOTS[i],
    note: size(t.bytes),
  }))
  if (rest.length) {
    const restBytes = rest.reduce((n, t) => n + t.bytes, 0)
    slices.push({
      key: 'other',
      label: rest.length === 1 ? name(rest[0].format) : 'Other',
      value: rest.reduce((n, t) => n + t.tracks, 0),
      color: 'var(--color-series-other)',
      note:
        rest.length > 1
          ? [size(restBytes), rest.map((t) => `${name(t.format)} ${formatCount(t.tracks)}`).join(', ')].filter(Boolean).join(' · ')
          : size(restBytes),
    })
  }
  const total = types.data.reduce((n, t) => n + t.bytes, 0)
  const partly = types.data.some((t) => t.sized < t.tracks)
  return (
    <Card title="Types of file">
      <Donut
        slices={slices}
        caption="Songs by type of file"
        unit={songs}
        center={sized ? { value: formatBytes(total), label: partly ? 'so far' : 'in all' } : undefined}
      />
      {sized && partly && (
        <p className="mt-3 text-xs text-muted">Not every file's size is known yet: the next full library sync fills them in.</p>
      )}
    </Card>
  )
}

/** How much of the library has been seen live (most people: very little, so it's a number,
 * not a chart), and the artists with the highest share of their songs seen live. */
function SeenLive({ serverId }: { serverId: number }) {
  const seen = useSeenLive(serverId)
  if (seen.isPending) return <Card title="Seen live"><Spinner /></Card>
  if (seen.isError) return <Card title="Seen live"><Notice tone="error">{errorMessage(seen.error)}</Notice></Card>
  const s = seen.data
  if (s.gigs === 0)
    return (
      <Card title="Seen live">
        <p className="text-sm text-muted">
          Add the gigs you’ve been to (on{' '}
          <Link to="/gigs/been-to" className="text-accent underline">
            Gigs → Been to
          </Link>
          ) to see how much of your library you’ve heard live. A setlist.fm link fills in what they played.
        </p>
      </Card>
    )
  return (
    <Card title="Seen live">
      <div className="grid gap-6 md:grid-cols-[minmax(0,15rem)_1fr]">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-1">
          <div>
            <p className="font-display text-4xl font-bold tabular-nums sm:text-5xl">{formatShare(s.artists_seen, s.artists_total)}</p>
            <p className="mt-1 text-sm text-muted">
              of the artists in your library: {formatCount(s.artists_seen)} of {formatCount(s.artists_total)}, from{' '}
              {plural(s.gigs, 'gig', 'gigs')}.
            </p>
          </div>
          <div>
            {s.setlists > 0 ? (
              <>
                <p className="font-display text-4xl font-bold tabular-nums sm:text-5xl">{formatShare(s.seen, s.tracks)}</p>
                <p className="mt-1 text-sm text-muted">
                  of the songs: {formatCount(s.seen)} of {formatCount(s.tracks)}, from the setlists of{' '}
                  {plural(s.setlists, 'gig', 'gigs')}.
                </p>
              </>
            ) : (
              <p className="text-sm text-muted">
                Songs seen live come from the gigs’ setlists: add a setlist.fm link to a gig to fill one in.
              </p>
            )}
          </div>
        </div>
        {s.artists.length > 0 ? (
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">Most of their songs seen live</p>
            <ol className="space-y-2" aria-label="Artists with the most of their songs seen live">
              {s.artists.map((a) => (
                <li key={a.rating_key}>
                  <Link to={`/library/artists/${a.rating_key}`} className="group flex items-center gap-3">
                    <Artwork serverId={serverId} path={a.thumb} title={a.title} size={64} round className="size-9 shrink-0 text-xs" />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-2 text-sm">
                        <span className="truncate font-medium group-hover:underline">{a.title}</span>
                        <span className="shrink-0 tabular-nums text-muted">
                          {formatCount(a.seen)} of {formatCount(a.tracks)} · <span className="font-semibold text-text">{formatShare(a.seen, a.tracks)}</span>
                        </span>
                      </span>
                      <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
                        <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.max(2, (a.seen / a.tracks) * 100)}%` }} />
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ol>
          </div>
        ) : (
          <p className="self-center text-sm text-muted">
            {s.seen === 0
              ? 'None of the songs in your gigs’ setlists are in this library yet.'
              : 'Artists show here once you’ve seen songs live by someone with at least 5 songs in your library.'}
          </p>
        )}
      </div>
    </Card>
  )
}

function Countries({ serverId }: { serverId: number }) {
  const countries = useArtistCountries(serverId)
  const [area, setArea] = useState<MapArea>({ level: 'world' })
  const code = area.level === 'country' ? area.country : null
  const artists = useCountryArtists(serverId, code)
  if (countries.isPending) return <Card title="Where artists are from"><Spinner /></Card>
  if (countries.isError) return <Notice tone="error">{errorMessage(countries.error)}</Notice>
  const c = countries.data
  const data = c.countries.map((x) => ({ country_code: x.country_code, continent_code: null, listens: x.artists }))
  const known = c.countries.reduce((n, x) => n + x.artists, 0)
  return (
    <Card title="Where artists are from">
      {area.level !== 'world' && (
        <nav aria-label="Map area" className="mb-2 flex flex-wrap items-center gap-1 text-sm">
          <button type="button" onClick={() => setArea({ level: 'world' })} className="text-accent hover:underline">
            World
          </button>
          {area.continent && (
            <>
              <span className="text-muted">›</span>
              {area.level === 'country' ? (
                <button type="button" onClick={() => setArea({ level: 'continent', continent: area.continent! })} className="text-accent hover:underline">
                  {CONTINENTS[area.continent] ?? area.continent}
                </button>
              ) : (
                <span className="font-semibold">{CONTINENTS[area.continent] ?? area.continent}</span>
              )}
            </>
          )}
          {area.level === 'country' && (
            <>
              <span className="text-muted">›</span>
              <span className="font-semibold">{countryName(area.country)}</span>
            </>
          )}
        </nav>
      )}
      <ListensMap countries={data} area={area} onArea={setArea} unit={['artist', 'artists']} />
      {code === null ? (
        c.countries.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label="Countries with the most artists">
            {c.countries.slice(0, 12).map((x) => (
              <button
                key={x.country_code}
                type="button"
                onClick={() => setArea({ level: 'country', continent: null, country: x.country_code })}
                className="rounded-full border border-border px-2.5 py-0.5 text-xs text-muted hover:text-text"
              >
                {countryName(x.country_code)} <span className="tabular-nums">{formatCount(x.artists)}</span>
              </button>
            ))}
          </div>
        )
      ) : artists.isPending ? (
        <Spinner />
      ) : artists.isError ? (
        <Notice tone="error">{errorMessage(artists.error)}</Notice>
      ) : (
        <ul className="mt-4 grid grid-cols-3 gap-3 sm:grid-cols-5 md:grid-cols-8">
          {artists.data.map((a) => (
            <li key={a.rating_key}>
              <Link to={`/library/artists/${a.rating_key}`} className="group block">
                <Artwork serverId={serverId} path={a.thumb} title={a.title} round className="w-full group-hover:opacity-85" />
                <div className="mt-1 truncate text-center text-xs font-medium">{a.title}</div>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-muted">
        {formatCount(known)} {known === 1 ? 'artist' : 'artists'} placed, from Plex's country or else MusicBrainz.
        {c.unknown > 0 && ` ${formatCount(c.unknown)} without a country`}
        {c.pending > 0 && ` (${formatCount(c.pending)} still to be looked up on MusicBrainz, a few hundred an hour)`}
        {c.unknown > 0 && '.'}
      </p>
    </Card>
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

function Colours({ serverId }: { serverId: number }) {
  const colours = useColours(serverId)
  const [chosen, setChosen] = useState<ColourName | null>(null)
  const covers = useColourFamily(serverId, chosen)
  if (colours.isPending) return <Card title="Cover colours"><Spinner /></Card>
  if (colours.isError) return <Notice tone="error">{errorMessage(colours.error)}</Notice>
  const c = colours.data
  const name = (n: string) => n[0].toUpperCase() + n.slice(1)
  return (
    <Card title="Cover colours">
      {c.albums === 0 ? (
        <p className="text-sm text-muted">
          {c.waiting > 0 ? 'The covers are being looked at; their colours show up here soon.' : 'No album covers yet.'}
        </p>
      ) : (
        <>
          <div className="flex h-8 overflow-hidden rounded-lg" aria-hidden>
            {c.strip.map((hex, i) => (
              <div key={i} className="min-w-0 flex-1" style={{ background: hex }} />
            ))}
          </div>
          <div className="mt-2 flex h-9 gap-0.5" role="group" aria-label="Colour families">
            {c.families.map((f) => {
              const on = f.name === chosen
              return (
                <button
                  key={f.name}
                  type="button"
                  aria-pressed={on}
                  aria-label={`${name(f.name)}: ${plural(f.albums, 'album', 'albums')}`}
                  title={`${name(f.name)}: ${plural(f.albums, 'album', 'albums')}`}
                  onClick={() => setChosen(on ? null : (f.name as ColourName))}
                  className={`min-w-2 rounded-md border-2 transition-transform hover:-translate-y-0.5 ${on ? 'border-text' : 'border-transparent'}`}
                  style={{ background: f.hex, flexGrow: f.albums, flexBasis: 0 }}
                />
              )
            })}
          </div>
          <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted" aria-hidden>
            {c.families.map((f) => (
              <li key={f.name} className="flex items-center gap-1">
                <span className="size-2.5 rounded-sm border border-border" style={{ background: f.hex }} />
                {name(f.name)} <span className="tabular-nums">{formatCount(f.albums)}</span>
              </li>
            ))}
          </ul>
          {chosen &&
            (covers.isPending ? (
              <Spinner />
            ) : covers.isError ? (
              <Notice tone="error">{errorMessage(covers.error)}</Notice>
            ) : (
              <ul className="mt-4 grid grid-cols-4 gap-2 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10">
                {covers.data.map((a) => (
                  <li key={a.rating_key}>
                    <Link to={`/library/albums/${a.rating_key}`} className="group block" title={`${a.title}${a.artist_title ? ` · ${a.artist_title}` : ''}`}>
                      <Artwork serverId={serverId} path={a.thumb} title={a.title} size={128} className="w-full group-hover:opacity-85" />
                      <div className="mt-1 h-1.5 rounded-full" style={{ background: a.hex }} aria-hidden />
                    </Link>
                  </li>
                ))}
              </ul>
            ))}
        </>
      )}
      <p className="mt-3 text-xs text-muted">
        Each cover's main colour, from a small copy of it. Click a colour for its covers.
        {c.waiting > 0 && ` ${plural(c.waiting, 'cover', 'covers')} still to be looked at (a few thousand every 15 minutes).`}
      </p>
    </Card>
  )
}

const WORD_KINDS: [WordKind, string][] = [
  ['track', 'Songs'],
  ['album', 'Albums'],
  ['artist', 'Artists'],
]

function Words({ serverId }: { serverId: number }) {
  const [kind, setKind] = useState<WordKind>('track')
  const [picked, setPicked] = useState<{ word: string; count: number } | null>(null)
  const words = useWords(serverId, kind)
  const shown = words.data ?? []
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
        <WordCloud words={shown} label={`The most common words in ${what}' names`} onPick={kind === 'track' ? setPicked : undefined} />
      )}
      <p className="mt-2 text-xs text-muted">
        The 100 most common words in {what}' names, sized by how many names they're in. Words like "the", "and" or
        "remastered" are left out (in several languages).{kind === 'track' && ' Click a word for a playlist of its songs.'}
      </p>
      {picked && <SongsAbout serverId={serverId} word={picked.word} count={picked.count} onClose={() => setPicked(null)} />}
    </Card>
  )
}

/** "Songs about <word>": a smart playlist of every song with the word in its title (found
 * again at each weekly refresh), made straight away or adjusted in the builder first. */
function SongsAbout({ serverId, word, count, onClose }: { serverId: number; word: string; count: number; onClose: () => void }) {
  const save = useSavePlaylist(serverId, null)
  const navigate = useNavigate()
  const name = `Songs about ${word}`
  return (
    <Dialog title={name} onClose={onClose}>
      <div className="space-y-4 text-sm">
        <p>
          Make a playlist of the {plural(count, 'song', 'songs')} with “{word}” in the title? It’s refreshed weekly, so
          new songs with it come in.
        </p>
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            busy={save.isPending}
            onClick={() =>
              save.mutate(
                {
                  name,
                  description: `Every song with “${word}” in the title. Made by Plekstender.`,
                  schedule: 'weekly',
                  definition: { title_words: [word], forgotten: false, order: 'random', limit: null },
                  display: {},
                },
                { onSuccess: () => navigate('/library/playlists') },
              )
            }
          >
            Make the playlist
          </Button>
          <Link
            to={`/library/playlists/new?word=${encodeURIComponent(word)}`}
            className="inline-flex items-center rounded-lg px-4 py-2 font-semibold text-muted hover:bg-surface-2 hover:text-text"
          >
            Adjust it first
          </Link>
        </div>
      </div>
    </Dialog>
  )
}
