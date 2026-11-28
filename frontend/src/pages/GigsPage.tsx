import { lazy, Suspense, useId, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import {
  useDeleteGig,
  useGigArtists,
  useGigs,
  useGigSuggestions,
  useRecordLiveGig,
  useSaveGig,
  useSetlistLookup,
  useSameVenue,
  NO_GIG_FILTERS,
  SETLIST_LINK,
  type Gig,
  type GigFilters,
  type GigPlace,
  type GigIn,
  useSetlistSearch,
  type GigSetlist,
  type SetlistLookup,
  type SetlistSearch,
} from '../api/gigs'
import { useActiveServer } from '../api/library'
import { useStarShow } from '../api/live'
import { DatePicker } from '../components/DatePicker'
import { Dialog } from '../components/Dialog'
import type { MapPoint } from '../components/GigMap'
import { GigPlacePicker, type PlaceChoice } from '../components/GigPlacePicker'
import { GigStats } from '../components/GigStats'
import { Select } from '../components/Select'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { localDate } from '../live'
import { countryName } from '../places'

const dayFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })

/** History → Gigs: the gigs you went to. */
export function GigsPage() {
  const [filters, setFilters] = useState<GigFilters>(NO_GIG_FILTERS)
  const [adding, setAdding] = useState(false)
  const gigs = useGigs(filters)
  const filtered = JSON.stringify(filters) !== JSON.stringify(NO_GIG_FILTERS)

  return (
    <div className="space-y-6">
      <Suggestions />
      <GigStats />
      <WhereMap
        focus={!filtered ? null : gigs.isPlaceholderData || !gigs.data ? undefined : gigs.data.gigs.flatMap((g) => (g.latitude != null && g.longitude != null ? [{ latitude: g.latitude, longitude: g.longitude }] : []))}
      />
      <div className="flex flex-wrap items-start gap-2">
        <GigFilterBar filters={filters} onChange={setFilters} years={gigs.data?.years ?? []} places={gigs.data?.places ?? []} unplaced={gigs.data?.unplaced ?? 0} />
        <Button variant="primary" className="ml-auto" onClick={() => setAdding(true)}>
          Add a gig
        </Button>
      </div>
      {adding && <GigDialog onClose={() => setAdding(false)} />}
      {filters.unplaced && (
        <p className="text-sm text-muted">
          These aren’t pinned at their venue on the map: only the town was found, or nothing was. Edit a gig to pick its
          place by hand.
        </p>
      )}
      {gigs.isPending ? (
        <Spinner />
      ) : gigs.isError ? (
        <Notice tone="error">{errorMessage(gigs.error)}</Notice>
      ) : gigs.data.gigs.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">
            {filtered
              ? 'No gigs match.'
              : 'No gigs yet. Add the ones you’ve been to, or star gigs on Live: once they’ve happened, they’re offered here.'}
          </p>
        </Card>
      ) : (
        <GigList gigs={gigs.data.gigs} />
      )}
      <p className="text-xs text-muted">
        Bring gigs in from setlist.fm, a CSV, or a calendar file, or take them with you, in{' '}
        <Link to="/settings/export/gigs" className="underline hover:text-text">
          Settings → Export &amp; import
        </Link>
        .
      </p>
    </div>
  )
}

const same = (a: string | null | undefined, b: string | null | undefined) => (a ?? '').toLowerCase() === (b ?? '').toLowerCase()
const byLabel = (a: { label: string }, b: { label: string }) => a.label.localeCompare(b.label)

/**
 * Artist, year, country, city and venue, and gigs not pinned at their venue. Picking a city
 * or venue also picks where it is; changing one clears the ones below it.
 */
function GigFilterBar({
  filters,
  onChange,
  years,
  places,
  unplaced,
}: {
  filters: GigFilters
  onChange: (f: GigFilters) => void
  years: { year: number; gigs: number }[]
  places: GigPlace[]
  unplaced: number
}) {
  const artists = useGigArtists()
  const { country, city, venue } = filters
  const set = (patch: Partial<GigFilters>) => onChange({ ...filters, ...patch })

  const countries = useMemo(
    () =>
      [...new Set(places.map((p) => p.country).filter((c): c is string => Boolean(c)))]
        .map((c) => ({ value: c, label: countryName(c) }))
        .sort(byLabel),
    [places],
  )
  const cities = useMemo(() => {
    const found = new Map<string, { country: string; city: string }>()
    for (const p of places) {
      if (!p.city || (country && p.country !== country)) continue
      const key = `${p.country ?? ''}|${p.city.toLowerCase()}`
      if (!found.has(key)) found.set(key, { country: p.country ?? '', city: p.city })
    }
    const list = [...found.values()]
    // The same name in two countries: say which is which.
    const twice = (c: string) => list.filter((o) => same(o.city, c)).length > 1
    return list
      .map((o) => ({
        value: JSON.stringify([o.country, o.city]),
        label: twice(o.city) && o.country ? `${o.city} · ${countryName(o.country)}` : o.city,
      }))
      .sort(byLabel)
  }, [places, country])
  const venues = useMemo(() => {
    const found = new Map<string, { country: string; city: string; venue: string }>()
    for (const p of places) {
      if (!p.venue || (country && p.country !== country) || (city && !same(p.city, city))) continue
      const key = `${p.country ?? ''}|${(p.city ?? '').toLowerCase()}|${p.venue.toLowerCase()}`
      if (!found.has(key)) found.set(key, { country: p.country ?? '', city: p.city ?? '', venue: p.venue })
    }
    return [...found.values()]
      .map((o) => ({ value: JSON.stringify([o.country, o.city, o.venue]), label: !city && o.city ? `${o.venue} · ${o.city}` : o.venue }))
      .sort(byLabel)
  }, [places, country, city])

  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter gigs">
      <Select
        label="Filter by artist"
        value={filters.artist}
        options={[{ value: '', label: 'All artists' }, ...(artists.data ?? []).map((a) => ({ value: a, label: a }))]}
        onChange={(artist) => set({ artist })}
      />
      <Select
        label="Filter by year"
        value={filters.year === null ? '' : String(filters.year)}
        options={[{ value: '', label: 'All years' }, ...years.map((y) => ({ value: String(y.year), label: String(y.year) }))]}
        onChange={(y) => set({ year: y ? Number(y) : null })}
      />
      {countries.length > 0 && (
        <Select
          label="Filter by country"
          value={country}
          options={[{ value: '', label: 'All countries' }, ...countries]}
          onChange={(c) => set({ country: c, city: '', venue: '' })}
        />
      )}
      {cities.length > 0 && (
        <Select
          label="Filter by city"
          value={city ? JSON.stringify([country, city]) : ''}
          options={[{ value: '', label: 'All cities' }, ...cities]}
          onChange={(v) => {
            const [c, town] = v ? (JSON.parse(v) as [string, string]) : [country, '']
            set({ country: c, city: town, venue: '' })
          }}
        />
      )}
      {venues.length > 0 && (
        <Select
          label="Filter by venue"
          value={venue ? JSON.stringify([country, city, venue]) : ''}
          options={[{ value: '', label: 'All venues' }, ...venues]}
          onChange={(v) => {
            if (!v) return set({ venue: '' })
            const [c, town, place] = JSON.parse(v) as [string, string, string]
            set({ country: c, city: town, venue: place })
          }}
        />
      )}
      {(unplaced > 0 || filters.unplaced) && (
        <button
          type="button"
          aria-pressed={filters.unplaced}
          onClick={() => set({ unplaced: !filters.unplaced })}
          className={`rounded-full px-3 py-1.5 text-sm font-medium ${
            filters.unplaced ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted hover:text-text'
          }`}
        >
          📍 Venue not found ({unplaced})
        </button>
      )}
      {JSON.stringify(filters) !== JSON.stringify(NO_GIG_FILTERS) && (
        <Button variant="ghost" onClick={() => onChange(NO_GIG_FILTERS)}>
          Clear filters
        </Button>
      )}
    </div>
  )
}

const GigMap = lazy(() => import('../components/GigMap'))

/** Every gig with a place, on the map; it frames the gigs the filters below pick out. */
function WhereMap({ focus }: { focus?: MapPoint[] | null }) {
  const all = useGigs()
  // The same list between renders, or the map is built again (and loses its place).
  const placed = useMemo(() => all.data?.gigs.filter((g) => g.latitude != null) ?? [], [all.data])
  if (placed.length === 0) return null
  const waiting = (all.data?.gigs.length ?? 0) - placed.length
  return (
    <Card title="Where">
      <Suspense fallback={<div className="h-[26rem]"><Spinner /></div>}>
        <GigMap gigs={placed} focus={focus} />
      </Suspense>
      {waiting > 0 && (
        <p className="mt-2 text-xs text-muted">
          {waiting} {waiting === 1 ? 'gig isn’t' : 'gigs aren’t'} on the map yet: places are looked up in the background
          (a venue a second), or there isn’t one to look up.
        </p>
      )}
    </Card>
  )
}

function Suggestions() {
  const suggestions = useGigSuggestions()
  const record = useRecordLiveGig()
  const star = useStarShow()
  if (!suggestions.data?.length) return null
  return (
    <Card title="Did you go?">
      <p className="mb-2 text-sm text-muted">Gigs you starred on Live that have happened.</p>
      <ul className="divide-y divide-border">
        {suggestions.data.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
            <span className="w-32 shrink-0 text-muted">{dayFormat.format(localDate(s.start_date))}</span>
            <span className="min-w-0 flex-1">
              <span className="font-medium">{s.artists.map((a) => a.name).join(', ') || s.name}</span>
              <span className="text-muted"> · {[s.venue, s.city].filter(Boolean).join(', ')}</span>
            </span>
            <Button variant="primary" busy={record.isPending && record.variables === s.ids} onClick={() => record.mutate(s.ids)}>
              I went
            </Button>
            <Button variant="ghost" onClick={() => star.mutate({ ids: s.ids, starred: false })}>
              Didn’t go
            </Button>
          </li>
        ))}
      </ul>
      {record.isError && <Notice tone="error">{errorMessage(record.error)}</Notice>}
    </Card>
  )
}

function GigList({ gigs }: { gigs: Gig[] }) {
  const { active } = useActiveServer()
  const years = useMemo(() => {
    const groups: [number, Gig[]][] = []
    for (const gig of gigs) {
      const y = Number(gig.date.slice(0, 4))
      const last = groups.at(-1)
      if (last?.[0] === y) last[1].push(gig)
      else groups.push([y, [gig]])
    }
    return groups
  }, [gigs])
  return (
    <div className="space-y-6">
      {years.map(([year, list]) => (
        <section key={year} className="space-y-2">
          <h2 className="text-sm font-semibold text-muted">
            {year} <span className="font-normal">· {list.length} {list.length === 1 ? 'gig' : 'gigs'}</span>
          </h2>
          <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-surface">
            {list.map((gig) => (
              <GigItem key={gig.id} gig={gig} serverId={active?.id ?? null} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

function GigItem({ gig, serverId }: { gig: Gig; serverId: number | null }) {
  const [editing, setEditing] = useState(false)
  const remove = useDeleteGig()
  const day = localDate(gig.date)
  const where = [gig.venue, gig.city, gig.country ? countryName(gig.country) : null].filter(Boolean).join(' · ')
  return (
    <li className="flex items-start gap-3 p-3">
      {editing && <GigDialog gig={gig} onClose={() => setEditing(false)} />}
      <div className="w-12 shrink-0 text-center">
        <div className="text-xs uppercase text-muted">{day.toLocaleDateString(undefined, { month: 'short' })}</div>
        <div className="text-xl font-bold tabular-nums leading-tight">{day.getDate()}</div>
        <div className="text-xs text-muted">{day.getFullYear()}</div>
      </div>
      <div className="min-w-0 flex-1 space-y-0.5 text-sm">
        <p className="font-medium">
          {gig.artists.map((a, i) => (
            <span key={a.name}>
              {i > 0 && ', '}
              {a.server_id === serverId && a.rating_key != null ? (
                <Link to={`/library/artists/${a.rating_key}`} className={i === 0 ? 'font-semibold hover:underline' : 'font-normal hover:underline'}>
                  {a.name}
                </Link>
              ) : (
                <span className={i === 0 ? 'font-semibold' : 'font-normal'}>{a.name}</span>
              )}
            </span>
          ))}
        </p>
        {gig.name && <p className="text-muted">{gig.name}</p>}
        {where && <p className="text-muted">{where}</p>}
        {gig.notes && <p className="whitespace-pre-line text-muted">{gig.notes}</p>}
      </div>
      <div className="flex shrink-0 gap-1">
        <Button variant="ghost" onClick={() => setEditing(true)} aria-label={`Edit the gig on ${gig.date}`}>
          Edit
        </Button>
        <Button
          variant="ghost"
          busy={remove.isPending}
          aria-label={`Delete the gig on ${gig.date}`}
          onClick={() => window.confirm('Delete this gig?') && remove.mutate(gig.id)}
        >
          Delete
        </Button>
      </div>
    </li>
  )
}

const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm'

/** The same venue to the server (case, accents and spacing aside), roughly. */
const placeKey = (...parts: (string | null | undefined)[]) =>
  parts.map((p) => (p ?? '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase().split(/\s+/).filter(Boolean).join(' ')).join('|')

const venueName = (venue: string | null | undefined, city: string | null | undefined) =>
  city?.trim() ? `${(venue ?? '').trim()} (${city.trim()})` : (venue ?? '').trim()

/** Before saving: should this go for the other gigs at the venue too? */
function OtherGigs({
  text,
  count,
  busy,
  onAnswer,
  onBack,
}: {
  text: string
  count: number
  busy: 'yes' | 'no' | null
  onAnswer: (yes: boolean) => void
  onBack: () => void
}) {
  return (
    <div className="space-y-2 rounded-lg border border-border bg-surface-2 p-3 text-sm sm:col-span-2" role="group" aria-label="Other gigs here">
      <p>{text}</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" busy={busy === 'yes'} disabled={busy !== null} onClick={() => onAnswer(true)}>
          Yes, all {count + 1} gigs
        </Button>
        <Button busy={busy === 'no'} disabled={busy !== null} onClick={() => onAnswer(false)}>
          Just this one
        </Button>
        <Button variant="ghost" disabled={busy !== null} onClick={onBack}>
          Back
        </Button>
      </div>
    </div>
  )
}

/** What a setlist.fm lookup filled in, in a sentence. */
function filledIn(found: SetlistLookup): string {
  const songs = found.setlists.reduce((n, s) => n + s.songs.length, 0)
  const acts = found.artists.length
  return (
    `Filled in from setlist.fm: ${acts} ${acts === 1 ? 'artist' : 'artists'}` +
    (songs ? ` and ${songs} ${songs === 1 ? 'song' : 'songs'} played` : '') +
    '. Check it over, then save.'
  )
}

/** Adding a gig (find it on setlist.fm, or add it by hand) or editing one, in a popup. */
function GigDialog({ gig, onClose }: { gig?: Gig; onClose: () => void }) {
  const [found, setFound] = useState<SetlistLookup | null>(null)
  const [byHand, setByHand] = useState(false)
  if (gig)
    return (
      <Dialog title="Edit the gig" onClose={onClose}>
        <GigForm gig={gig} onDone={onClose} />
      </Dialog>
    )
  if (found || byHand)
    return (
      <Dialog title="Add a gig" onClose={onClose}>
        <button
          type="button"
          className="mb-3 text-sm text-muted hover:text-text"
          onClick={() => {
            setFound(null)
            setByHand(false)
          }}
        >
          ← Find another on setlist.fm
        </button>
        <GigForm key={found?.url ?? 'by-hand'} found={found ?? undefined} onDone={onClose} />
      </Dialog>
    )
  return (
    <Dialog title="Add a gig" subtitle="Find it on setlist.fm to fill in the night, the bill, and what was played." onClose={onClose}>
      <GigFinder onFound={setFound} onByHand={() => setByHand(true)} />
    </Dialog>
  )
}

const YEAR = /^\d{4}$/
const shortDay = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

/** Step one of adding a gig: a setlist.fm link, or a search of setlist.fm. */
function GigFinder({ onFound, onByHand }: { onFound: (found: SetlistLookup) => void; onByHand: () => void }) {
  const lookup = useSetlistLookup()
  const [link, setLink] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [artist, setArtist] = useState('')
  const [year, setYear] = useState('')
  const [city, setCity] = useState('')
  const [search, setSearch] = useState<SetlistSearch | null>(null)
  const results = useSetlistSearch(search)
  const id = useId()
  const open = (url: string) => {
    setPicked(url)
    lookup.mutate(url, { onSuccess: onFound })
  }
  const canSearch = Boolean(artist.trim() || city.trim() || YEAR.test(year))
  const pages = results.data ? Math.max(1, Math.ceil(results.data.total / 20)) : 1
  return (
    <div className="space-y-5 text-sm">
      <div className="space-y-1">
        <label htmlFor={`${id}-link`} className="block font-medium">
          A setlist.fm link
        </label>
        <input
          id={`${id}-link`}
          type="url"
          value={link}
          placeholder="https://www.setlist.fm/setlist/…"
          onChange={(e) => {
            setLink(e.target.value)
            if (SETLIST_LINK.test(e.target.value)) open(e.target.value)
          }}
          className={field}
        />
        {lookup.isPending && picked === link && <p className="text-xs text-muted">Looking it up on setlist.fm…</p>}
      </div>

      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (canSearch) setSearch({ artist: artist.trim(), year: YEAR.test(year) ? year : '', city: city.trim(), page: 1 })
        }}
      >
        <p className="font-medium">Or search setlist.fm</p>
        <div className="grid gap-2 sm:grid-cols-[1fr_6rem_1fr_auto]">
          <input aria-label="Artist" placeholder="Artist" value={artist} onChange={(e) => setArtist(e.target.value)} className={field} />
          <input aria-label="Year" placeholder="Year" inputMode="numeric" maxLength={4} value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, ''))} className={field} />
          <input aria-label="Town or city" placeholder="Town or city" value={city} onChange={(e) => setCity(e.target.value)} className={field} />
          <Button type="submit" variant="primary" disabled={!canSearch} busy={results.isFetching}>
            Search
          </Button>
        </div>
      </form>

      {results.isError && <Notice tone="error">{errorMessage(results.error)}</Notice>}
      {lookup.isError && <Notice tone="error">{errorMessage(lookup.error)}</Notice>}
      {results.data &&
        (results.data.items.length === 0 ? (
          <p className="text-muted">Nothing found on setlist.fm. Try fewer words, or add it by hand.</p>
        ) : (
          <div className={results.isPlaceholderData ? 'opacity-60' : ''}>
            <ul className="-mx-2 divide-y divide-border" aria-label="Setlists found">
              {results.data.items.map((r) => (
                <li key={r.url}>
                  <button
                    type="button"
                    disabled={lookup.isPending}
                    onClick={() => open(r.url)}
                    className="flex w-full items-start gap-3 rounded-lg px-2 py-2 text-left hover:bg-surface-2 disabled:opacity-60"
                  >
                    <span className="w-24 shrink-0 whitespace-nowrap text-xs tabular-nums text-muted">{shortDay.format(localDate(r.date))}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{r.artist}</span>
                      <span className="block truncate text-xs text-muted">
                        {[r.tour, r.venue, r.city, r.country ? countryName(r.country) : null].filter(Boolean).join(' · ')}
                      </span>
                    </span>
                    <span className="shrink-0 text-xs text-muted">
                      {lookup.isPending && picked === r.url ? <Spinner small /> : r.songs ? `${r.songs} songs` : 'No setlist'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {pages > 1 && search && (
              <div className="mt-2 flex items-center justify-between text-xs text-muted">
                <Button variant="ghost" disabled={search.page <= 1} onClick={() => setSearch({ ...search, page: search.page - 1 })}>
                  ‹ Newer
                </Button>
                <span className="tabular-nums">
                  Page {search.page} of {pages}
                </span>
                <Button variant="ghost" disabled={search.page >= pages} onClick={() => setSearch({ ...search, page: search.page + 1 })}>
                  Older ›
                </Button>
              </div>
            )}
          </div>
        ))}

      <p className="border-t border-border pt-3 text-muted">
        Not on setlist.fm?{' '}
        <button type="button" className="text-accent hover:underline" onClick={onByHand}>
          Add it by hand
        </button>
      </p>
    </div>
  )
}

function GigForm({ gig, found, onDone }: { gig?: Gig; found?: SetlistLookup; onDone: () => void }) {
  const save = useSaveGig()
  const lookup = useSetlistLookup()
  const start = gig ?? found
  const [date, setDate] = useState(start?.date ?? '')
  const [artists, setArtists] = useState(gig ? gig.artists.map((a) => a.name).join('\n') : (found?.artists.join('\n') ?? ''))
  const [name, setName] = useState(start?.name ?? '')
  const [venue, setVenue] = useState(start?.venue ?? '')
  const [city, setCity] = useState(start?.city ?? '')
  const [country, setCountry] = useState(start?.country ?? '')
  const [notes, setNotes] = useState(gig?.notes ?? '')
  const [url, setUrl] = useState(start?.url ?? '')
  const [setlists, setSetlists] = useState<GigSetlist[]>(start?.setlists ?? [])
  const [filled, setFilled] = useState<string | null>(found ? filledIn(found) : null)
  const [place, setPlace] = useState<PlaceChoice>({ picked: null, findAgain: false })
  // Ask about the other gigs at the venue before saving: first whether a renamed venue is
  // renamed on them too, then whether a place picked by hand goes for them too.
  const [asking, setAsking] = useState<'rename' | 'place' | null>(null)
  const [renameOthers, setRenameOthers] = useState(false)
  const renamed = Boolean(gig?.venue) && placeKey(gig?.venue, gig?.city, gig?.country) !== placeKey(venue, city, country)
  const atOldVenue = useSameVenue(gig?.venue ?? '', gig?.city ?? '', gig?.country ?? '', gig?.id, renamed)
  const toRename = renamed ? (atOldVenue.data?.gigs ?? 0) : 0
  const atVenue = useSameVenue(venue, city, country, gig?.id, Boolean(place.picked))
  const toPlace = place.picked ? (atVenue.data?.gigs ?? 0) + (renameOthers ? toRename : 0) : 0
  const names = artists.split('\n').map((a) => a.trim()).filter(Boolean)
  const dateId = useId()
  const valid = Boolean(date) && names.length > 0 && (!country || /^[A-Za-z]{2}$/.test(country)) && (!url || /^https?:\/\//.test(url))
  const body: GigIn = {
    date,
    artists: names,
    name: name || null,
    venue: venue || null,
    city: city || null,
    country: country || null,
    notes: notes || null,
    url: url || null,
    setlists,
    place: place.picked ? { latitude: place.picked.latitude, longitude: place.picked.longitude } : null,
    find_place: place.findAgain,
    place_others: false,
    rename_others: false,
  }
  const submit = (rename: boolean, placeOthers: boolean) =>
    save.mutate({ id: gig?.id, gig: { ...body, rename_others: rename, place_others: placeOthers } }, { onSuccess: onDone })
  const afterRename = (rename: boolean) => {
    setRenameOthers(rename)
    const placeCount = place.picked ? (atVenue.data?.gigs ?? 0) + (rename ? toRename : 0) : 0
    if (placeCount > 0) setAsking('place')
    else submit(rename, false)
  }
  /** Fill the form in from a setlist.fm link: the night, the whole bill and the setlists. */
  const fillIn = (link: string) =>
    lookup.mutate(link, {
      onSuccess: (got) => {
        setDate(got.date)
        setArtists(got.artists.join('\n'))
        setName(got.name ?? '')
        setVenue(got.venue ?? '')
        setCity(got.city ?? '')
        setCountry(got.country ?? '')
        setSetlists(got.setlists)
        setFilled(filledIn(got))
      },
    })
  const onUrl = (next: string) => {
    setUrl(next)
    setFilled(null)
    // A setlist.fm link pasted in: fill the rest in straight away.
    if (SETLIST_LINK.test(next) && !SETLIST_LINK.test(url)) fillIn(next)
  }
  return (
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (!valid) return
          if (toRename > 0) setAsking('rename')
          else if (toPlace > 0) setAsking('place')
          else submit(false, false)
        }}
      >
        <div className="space-y-1 text-sm sm:col-span-2">
          <label htmlFor={`${dateId}-link`} className="block font-medium">
            Link (setlist, tickets…)
          </label>
          <div className="flex gap-2">
            <input id={`${dateId}-link`} type="url" value={url} onChange={(e) => onUrl(e.target.value)} placeholder="https://" className={field} />
            {SETLIST_LINK.test(url) && (
              <Button type="button" variant="ghost" busy={lookup.isPending} onClick={() => fillIn(url)} className="shrink-0">
                Fill in from setlist.fm
              </Button>
            )}
          </div>
          <p className="text-xs text-muted">
            {lookup.isPending
              ? 'Looking it up on setlist.fm…'
              : filled ?? 'Paste a setlist.fm link to fill in the rest: the date, the venue, everyone on the bill, and what they played.'}
          </p>
          {lookup.isError && <Notice tone="error">{errorMessage(lookup.error)}</Notice>}
        </div>
        <div className="space-y-1 text-sm">
          <span id={dateId} className="block font-medium">
            Date
          </span>
          <DatePicker value={date} onChange={setDate} labelledBy={dateId} className="w-full" />
        </div>
        <label className="space-y-1 text-sm">
          <span className="block font-medium">Festival or tour (optional)</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className={field} />
        </label>
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="block font-medium">Artists (one a line, the headliner first)</span>
          <textarea required rows={3} value={artists} onChange={(e) => setArtists(e.target.value)} className={field} />
        </label>
        <label className="space-y-1 text-sm">
          <span className="block font-medium">Venue</span>
          <input value={venue} onChange={(e) => setVenue(e.target.value)} className={field} />
        </label>
        <div className="grid grid-cols-[1fr_6rem] gap-3">
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Town or city</span>
            <input value={city} onChange={(e) => setCity(e.target.value)} className={field} />
          </label>
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Country</span>
            <input value={country} maxLength={2} placeholder="GB" onChange={(e) => setCountry(e.target.value.toUpperCase())} className={field} />
          </label>
        </div>
        <GigPlacePicker gig={gig} venue={venue} city={city} country={country} choice={place} onChange={setPlace} />
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="block font-medium">Notes</span>
          <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} className={field} />
        </label>
        {setlists.length > 0 && (
          <div className="text-sm sm:col-span-2">
            <p className="font-medium">Setlists</p>
            <p className="text-muted">
              {setlists.map((s) => `${s.artist} (${s.songs.length} ${s.songs.length === 1 ? 'song' : 'songs'})`).join(', ')}. The
              songs in your library are marked as seen live.{' '}
              <button type="button" className="text-accent hover:underline" onClick={() => setSetlists([])}>
                Remove
              </button>
            </p>
          </div>
        )}
        {asking === 'rename' ? (
          <OtherGigs
            text={`You’ve been to ${toRename} other ${toRename === 1 ? 'gig' : 'gigs'} at ${venueName(gig?.venue, gig?.city)}. Change ${
              toRename === 1 ? 'it' : 'them'
            } to ${venueName(venue, city)} too?`}
            count={toRename}
            busy={save.isPending ? (save.variables?.gig.rename_others ? 'yes' : 'no') : null}
            onAnswer={afterRename}
            onBack={() => setAsking(null)}
          />
        ) : asking === 'place' ? (
          <OtherGigs
            text={`You’ve been to ${toPlace} other ${toPlace === 1 ? 'gig' : 'gigs'} at ${venueName(venue, city)}. Put ${
              toPlace === 1 ? 'it' : 'them'
            } at this place on the map too?`}
            count={toPlace}
            busy={save.isPending ? (save.variables?.gig.place_others ? 'yes' : 'no') : null}
            onAnswer={(yes) => submit(renameOthers, yes)}
            onBack={() => (setRenameOthers(false), setAsking(null))}
          />
        ) : (
          <div className="flex gap-2 sm:col-span-2">
            <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending}>
              Save
            </Button>
            <Button variant="ghost" onClick={onDone}>
              Cancel
            </Button>
          </div>
        )}
        {save.isError && (
          <div className="sm:col-span-2">
            <Notice tone="error">{errorMessage(save.error)}</Notice>
          </div>
        )}
      </form>
  )
}
