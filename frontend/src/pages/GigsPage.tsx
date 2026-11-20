import { useId, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import {
  useDeleteGig,
  useGigArtists,
  useGigs,
  useGigSuggestions,
  useRecordLiveGig,
  useSaveGig,
  type Gig,
  type GigIn,
} from '../api/gigs'
import { useActiveServer } from '../api/library'
import { useStarShow } from '../api/live'
import { Select } from '../components/Select'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { localDate } from '../live'
import { countryName } from '../places'

const dayFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })

/** History → Gigs: the gigs you went to. */
export function GigsPage() {
  const [year, setYear] = useState<number | null>(null)
  const [artist, setArtist] = useState('')
  const [adding, setAdding] = useState(false)
  const gigs = useGigs(year, artist)
  const artists = useGigArtists()
  const artistId = useId()

  return (
    <div className="space-y-6">
      <Suggestions />
      <div className="flex flex-wrap items-center gap-2">
        <span id={artistId} className="sr-only">
          Artist
        </span>
        <Select
          labelledBy={artistId}
          value={artist}
          options={[{ value: '', label: 'All artists' }, ...(artists.data ?? []).map((a) => ({ value: a, label: a }))]}
          onChange={setArtist}
        />
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Year">
          {[null, ...(gigs.data?.years ?? []).map((y) => y.year)].map((y) => (
            <button
              key={y ?? 'all'}
              type="button"
              aria-pressed={year === y}
              onClick={() => setYear(y)}
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                year === y ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted hover:text-text'
              }`}
            >
              {y ?? 'All years'}
            </button>
          ))}
        </div>
        <Button variant="primary" className="ml-auto" onClick={() => setAdding(true)}>
          Add a gig
        </Button>
      </div>
      {adding && <GigForm onDone={() => setAdding(false)} />}
      {gigs.isPending ? (
        <Spinner />
      ) : gigs.isError ? (
        <Notice tone="error">{errorMessage(gigs.error)}</Notice>
      ) : gigs.data.gigs.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">
            {year || artist
              ? 'No gigs match.'
              : 'No gigs yet. Add the ones you’ve been to, or star gigs on Live: once they’ve happened, they’re offered here.'}
          </p>
        </Card>
      ) : (
        <GigList gigs={gigs.data.gigs} />
      )}
    </div>
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
  if (editing)
    return (
      <li className="p-3">
        <GigForm gig={gig} onDone={() => setEditing(false)} />
      </li>
    )
  return (
    <li className="flex items-start gap-3 p-3">
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

function GigForm({ gig, onDone }: { gig?: Gig; onDone: () => void }) {
  const save = useSaveGig()
  const [date, setDate] = useState(gig?.date ?? '')
  const [artists, setArtists] = useState(gig?.artists.map((a) => a.name).join('\n') ?? '')
  const [name, setName] = useState(gig?.name ?? '')
  const [venue, setVenue] = useState(gig?.venue ?? '')
  const [city, setCity] = useState(gig?.city ?? '')
  const [country, setCountry] = useState(gig?.country ?? '')
  const [notes, setNotes] = useState(gig?.notes ?? '')
  const [url, setUrl] = useState(gig?.url ?? '')
  const names = artists.split('\n').map((a) => a.trim()).filter(Boolean)
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
  }
  return (
    <Card title={gig ? 'Edit the gig' : 'Add a gig'}>
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (valid) save.mutate({ id: gig?.id, gig: body }, { onSuccess: onDone })
        }}
      >
        <label className="space-y-1 text-sm">
          <span className="block font-medium">Date</span>
          <input type="date" required value={date} onChange={(e) => setDate(e.target.value)} className={field} />
        </label>
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
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="block font-medium">Notes</span>
          <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} className={field} />
        </label>
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="block font-medium">Link (setlist, tickets…)</span>
          <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" className={field} />
        </label>
        <div className="flex gap-2 sm:col-span-2">
          <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending}>
            Save
          </Button>
          <Button variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        </div>
        {save.isError && (
          <div className="sm:col-span-2">
            <Notice tone="error">{errorMessage(save.error)}</Notice>
          </div>
        )}
      </form>
    </Card>
  )
}
