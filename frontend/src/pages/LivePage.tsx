import { useDeferredValue, useEffect, useId, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useMe } from '../api/hooks'
import { useActiveServer } from '../api/library'
import {
  useCalendarAddress,
  useAddLiveFeed,
  useForgetLiveSettings,
  useGotTicket,
  useListenedCount,
  useLiveSettings,
  useLiveShows,
  usePlaces,
  useLiveFeeds,
  useRefreshLive,
  useRemoveLiveFeed,
  useResetCalendar,
  useSaveLiveSettings,
  useStarShow,
  type LiveSettings,
  type LiveShow,
  type Place,
} from '../api/live'
import { Slider } from '../components/controls'
import { Select } from '../components/Select'
import { Button, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { formatRelative } from '../format'
import { describePeriod, followCount, formatPrice } from '../live'
import { countryName } from '../places'
import dataThistleLogo from '../assets/datathistle.svg'

const DISTANCES = [25, 50, 100, 200, 500, 1000]
type Range = 'all' | '30' | '90' | 'starred'
const RANGES: [Range, string][] = [
  ['all', 'Any time'],
  ['30', 'Next 30 days'],
  ['90', 'Next 3 months'],
  ['starred', '★ Starred'],
]
const SOURCE_NAMES: Record<string, string> = {
  jambase: 'JamBase',
  ticketmaster: 'Ticketmaster',
  datathistle: 'Data Thistle',
  calendar: 'your calendar link',
}

/** "2027-03-04" as a local date (no time zone shift). */
function localDate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function LivePage() {
  const settings = useLiveSettings()
  const [editing, setEditing] = useState(false)
  return (
    <>
      <PageTitle subtitle="Upcoming shows near you by the artists you listen to most.">Live</PageTitle>
      {settings.isPending ? (
        <Spinner />
      ) : settings.isError ? (
        <Notice tone="error">{errorMessage(settings.error)}</Notice>
      ) : !settings.data.place_name || editing ? (
        <div className="max-w-xl">
          <SetupCard settings={settings.data} onDone={() => setEditing(false)} onCancel={settings.data.place_name ? () => setEditing(false) : undefined} />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_18rem]">
          <Shows settings={settings.data} />
          <div className="order-first space-y-4 lg:order-none">
            <Summary settings={settings.data} onEdit={() => setEditing(true)} />
            <CalendarCard />
            <FeedsCard />
          </div>
        </div>
      )}
    </>
  )
}

function SourceMissing() {
  const me = useMe()
  return (
    <Notice tone="warn">
      Live needs a JamBase or Ticketmaster API key (or a calendar link of yours).{' '}
      {me.data?.is_admin ? (
        <>
          Add one in{' '}
          <Link to="/settings/connections" className="underline">
            Settings → Connections
          </Link>
          .
        </>
      ) : (
        'Ask an admin to add one.'
      )}
    </Notice>
  )
}

function Summary({ settings, onEdit }: { settings: LiveSettings; onEdit: () => void }) {
  const refresh = useRefreshLive()
  const busy = settings.refresh_status === 'queued' || settings.refresh_status === 'running'
  return (
    <Card>
      <div className="space-y-3 text-sm">
        <p>
          Within <strong>{settings.radius_km} km</strong> of <strong>{settings.place_name}</strong>, for your top{' '}
          {settings.top_percent}% of {describePeriod(settings.period_months)} ({settings.followed_artists} of{' '}
          {settings.listened_artists} artists you've played).
        </p>
        <p className="text-muted" aria-live="polite">
          {busy
            ? 'Looking up shows…'
            : settings.refreshed_at
              ? `Updated ${formatRelative(settings.refreshed_at)}. Shows are looked up about once a day.`
              : 'Not looked up yet.'}
        </p>
        {settings.refresh_status === 'failed' && settings.refresh_error && <Notice tone="warn">{settings.refresh_error}</Notice>}
        {settings.datathistle_error && <Notice tone="warn">Data Thistle: {settings.datathistle_error}</Notice>}
        {!settings.source_ready && <SourceMissing />}
        <div className="flex flex-wrap gap-2">
          <Button onClick={onEdit}>Change</Button>
          <Button
            variant="ghost"
            busy={refresh.isPending}
            disabled={busy || !settings.source_ready || Boolean(settings.refresh_available_at)}
            onClick={() => refresh.mutate()}
          >
            Refresh
          </Button>
        </div>
        {settings.refresh_available_at && !busy && (
          <p className="text-xs text-muted">You can refresh again {formatRelative(settings.refresh_available_at)}.</p>
        )}
        {refresh.isError && <Notice tone="error">{errorMessage(refresh.error)}</Notice>}
      </div>
    </Card>
  )
}

function SetupCard({ settings, onDone, onCancel }: { settings: LiveSettings; onDone: () => void; onCancel?: () => void }) {
  const save = useSaveLiveSettings()
  const forget = useForgetLiveSettings()
  const [place, setPlace] = useState<Place | null>(
    settings.place_name && settings.latitude != null && settings.longitude != null
      ? { name: settings.place_name, latitude: settings.latitude, longitude: settings.longitude, country: null }
      : null,
  )
  const [query, setQuery] = useState('')
  const [radius, setRadius] = useState(settings.radius_km)
  const [percent, setPercent] = useState(settings.top_percent)
  const saved = settings.period_months
  const [periodMode, setPeriodMode] = useState<'all' | 'past'>(saved ? 'past' : 'all')
  const [periodUnit, setPeriodUnit] = useState<'months' | 'years'>(saved && saved % 12 !== 0 ? 'months' : 'years')
  const [periodCount, setPeriodCount] = useState(saved ? (saved % 12 === 0 ? saved / 12 : saved) : 1)
  const periodMonths =
    periodMode === 'all' ? null : Math.max(1, Math.min(600, Math.round(periodCount || 1) * (periodUnit === 'years' ? 12 : 1)))
  const listenedNow = useListenedCount(periodMonths)
  const listened = listenedNow.data?.listened_artists ?? settings.listened_artists
  const periodId = useId()
  const [locating, setLocating] = useState(false)
  const [locateError, setLocateError] = useState<string | null>(null)
  const q = useDeferredValue(query.trim())
  const places = usePlaces(q)
  const distanceId = useId()
  const following = followCount(listened, percent)

  const useMyLocation = () => {
    if (!navigator.geolocation) {
      setLocateError("This browser can't share its location.")
      return
    }
    setLocating(true)
    setLocateError(null)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false)
        // About a kilometre is precise enough, and that's all that's stored.
        const round = (v: number) => Math.round(v * 100) / 100
        setPlace({ name: 'My location', latitude: round(pos.coords.latitude), longitude: round(pos.coords.longitude), country: null })
        setQuery('')
      },
      () => {
        setLocating(false)
        setLocateError("Your location isn't available; search for a place instead.")
      },
      { timeout: 15000, maximumAge: 600000 },
    )
  }

  return (
    <Card title="Where are you?">
      <div className="space-y-4">
        {!settings.source_ready && <SourceMissing />}
        <div className="space-y-2">
          {place ? (
            <div className="flex items-center justify-between gap-2 rounded-lg bg-surface-2 px-3 py-2 text-sm">
              <span>
                <span className="font-medium">{place.name}</span>
              </span>
              <button type="button" className="text-xs text-accent underline" onClick={() => setPlace(null)}>
                Change place
              </button>
            </div>
          ) : (
            <>
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search for a city"
                aria-label="Search for a city"
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
              />
              {q.length >= 2 &&
                (places.isPending ? (
                  <Spinner small />
                ) : places.isError ? (
                  <Notice tone="error">{errorMessage(places.error)}</Notice>
                ) : places.data.length === 0 ? (
                  <p className="text-sm text-muted">No places found.</p>
                ) : (
                  <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border" aria-label="Places">
                    {places.data.map((p) => (
                      <li key={`${p.latitude},${p.longitude}`}>
                        <button type="button" className="w-full px-3 py-2 text-left text-sm hover:bg-surface-2" onClick={() => setPlace(p)}>
                          {p.name}
                        </button>
                      </li>
                    ))}
                  </ul>
                ))}
              <Button variant="ghost" busy={locating} onClick={useMyLocation}>
                Use my location
              </Button>
              {locateError && <p className="text-xs text-danger">{locateError}</p>}
            </>
          )}
        </div>

        <div className="space-y-1">
          <span id={distanceId} className="block text-sm">
            How far you'd go
          </span>
          <Select
            labelledBy={distanceId}
            value={radius}
            options={(DISTANCES.includes(radius) ? DISTANCES : [...DISTANCES, radius].sort((a, b) => a - b)).map((d) => ({
              value: d,
              label: `${d} km`,
            }))}
            onChange={setRadius}
          />
        </div>

        <div className="space-y-1">
          <span id={periodId} className="block text-sm">
            Most played over
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              labelledBy={periodId}
              value={periodMode}
              options={[
                { value: 'all', label: 'All time' },
                { value: 'past', label: 'The past…' },
              ]}
              onChange={setPeriodMode}
            />
            {periodMode === 'past' && (
              <>
                <input
                  type="number"
                  min={1}
                  max={periodUnit === 'years' ? 50 : 600}
                  value={periodCount}
                  onChange={(e) => setPeriodCount(Number(e.target.value))}
                  aria-label="How many"
                  className="w-20 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm tabular-nums"
                />
                <Select
                  label="Months or years"
                  value={periodUnit}
                  options={[
                    { value: 'months', label: periodCount === 1 ? 'month' : 'months' },
                    { value: 'years', label: periodCount === 1 ? 'year' : 'years' },
                  ]}
                  onChange={setPeriodUnit}
                />
              </>
            )}
          </div>
        </div>

        <div className="space-y-1">
          <Slider label="Artists to follow" value={percent} min={1} max={100} step={1} format={(v) => `Top ${v}%`} onChange={setPercent} />
          <p className="text-xs text-muted">
            {listened === 0
              ? periodMode === 'all'
                ? "You haven't played any artists yet; shows are found for the artists you play."
                : `You haven't played any artists in ${describePeriod(periodMonths)}.`
              : `Your ${following} most played of the ${listened} artists you've played in ${describePeriod(periodMonths)}.${
                  following > 150 ? ' Following many artists uses more of the shared JamBase allowance, so shows are refreshed less often.' : ''
                }`}
          </p>
        </div>

        <p className="text-xs text-muted">
          Only the place is stored, and it's used just to look up shows (about once a day).
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={!place}
            busy={save.isPending}
            onClick={() =>
              place &&
              save.mutate(
                {
                  place_name: place.name,
                  latitude: place.latitude,
                  longitude: place.longitude,
                  radius_km: radius,
                  top_percent: percent,
                  period_months: periodMonths,
                },
                { onSuccess: onDone },
              )
            }
          >
            Save
          </Button>
          {onCancel && (
            <Button variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          )}
          {settings.place_name && (
            <Button
              variant="ghost"
              busy={forget.isPending}
              onClick={() => window.confirm('Forget your place, the shows and your starred shows?') && forget.mutate(undefined, { onSuccess: onDone })}
            >
              Forget my place
            </Button>
          )}
        </div>
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}

function Shows({ settings }: { settings: LiveSettings }) {
  const [artist, setArtist] = useState('')
  const [venue, setVenue] = useState('')
  const [range, setRange] = useState<Range>('all')
  const [to, setTo] = useState<string | undefined>(undefined)
  const chooseRange = (key: Range) => {
    setRange(key)
    if (key === 'all' || key === 'starred') return setTo(undefined)
    const d = new Date()
    d.setDate(d.getDate() + Number(key))
    setTo(isoDate(d))
  }
  const busy = settings.refresh_status === 'queued' || settings.refresh_status === 'running'
  // A Data Thistle lookup runs in the background when the tab is opened: look again for a bit.
  const [opened] = useState(() => Date.now())
  const dtPending =
    settings.sources.includes('datathistle') &&
    settings.datathistle_at != null &&
    Date.parse(settings.datathistle_at) > opened - 60_000
  const [watching, setWatching] = useState(true)
  useEffect(() => {
    const stop = setTimeout(() => setWatching(false), 60_000)
    return () => clearTimeout(stop)
  }, [])
  const shows = useLiveShows({ artist, venue, to, starred: range === 'starred' }, busy || (dtPending && watching))
  const { active } = useActiveServer()
  const artistId = useId()
  const venueId = useId()

  const months = useMemo(() => {
    const groups: [string, LiveShow[]][] = []
    for (const show of shows.data?.shows ?? []) {
      const label = localDate(show.start_date).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
      const last = groups.at(-1)
      if (last?.[0] === label) last[1].push(show)
      else groups.push([label, [show]])
    }
    return groups
  }, [shows.data])

  if (shows.isPending) return <Spinner />
  if (shows.isError) return <Notice tone="error">{errorMessage(shows.error)}</Notice>
  const artists = shows.data.artists
  const venues = shows.data.venues
  return (
    <div className="min-w-0 space-y-4">
      {(artists.length > 0 || artist || venue) && (
        <div className="flex flex-wrap items-center gap-2">
          <span id={artistId} className="sr-only">
            Artist
          </span>
          <Select
            labelledBy={artistId}
            value={artist}
            options={[
              { value: '', label: 'All artists' },
              ...(artist && !artists.some((a) => a.name === artist) ? [{ value: artist, label: artist }] : []),
              ...artists.map((a) => ({ value: a.name, label: `${a.name} (${a.shows})` })),
            ]}
            onChange={setArtist}
          />
          <span id={venueId} className="sr-only">
            Venue
          </span>
          <Select
            labelledBy={venueId}
            value={venue}
            options={[
              { value: '', label: 'All venues' },
              ...(venue && !venues.some((v) => v.name === venue) ? [{ value: venue, label: venue }] : []),
              ...venues.map((v) => ({ value: v.name, label: `${v.name}${v.city ? `, ${v.city}` : ''} (${v.shows})` })),
            ]}
            onChange={setVenue}
          />
          {artist && (
            <a
              href={`https://www.songkick.com/search?type=artists&query=${encodeURIComponent(artist)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-muted underline hover:text-accent"
            >
              Find {artist} on Songkick
            </a>
          )}
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="When">
            {RANGES.map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={range === key}
                onClick={() => chooseRange(key)}
                className={`rounded-full px-3 py-1 text-xs font-medium ${
                  range === key ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted hover:text-text'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {shows.data.shows.length === 0 ? (
        busy ? (
          <p className="text-sm text-muted">Looking up shows…</p>
        ) : settings.refreshed_at ? (
          <Card>
            <p className="text-sm text-muted">
              {range === 'starred'
                ? 'No starred shows yet: star a show to keep it and put it in your calendar.'
                : artist || venue || range !== 'all'
                ? 'No shows match these filters.'
                : `No upcoming shows within ${settings.radius_km} km by your top ${settings.followed_artists} artists. A larger distance or more artists may find some.`}
            </p>
          </Card>
        ) : null
      ) : (
        months.map(([label, list]) => (
          <section key={label} className="space-y-2">
            <h2 className="text-sm font-semibold text-muted">{label}</h2>
            <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-surface">
              {list.map((show) => (
                <ShowRow key={show.id} show={show} serverId={active?.id ?? null} />
              ))}
            </ul>
          </section>
        ))
      )}

      <p className="flex flex-wrap gap-x-3 text-xs text-muted">
        {settings.sources.includes('jambase') && (
          <a href="https://www.jambase.com" target="_blank" rel="nofollow noopener noreferrer" className="underline">
            Powered by JamBase
          </a>
        )}
        {settings.sources.includes('ticketmaster') && (
          <a href="https://www.ticketmaster.com" target="_blank" rel="nofollow noopener noreferrer" className="underline">
            Shows also from Ticketmaster
          </a>
        )}
        {settings.sources.includes('datathistle') && (
          <span className="flex items-center gap-2">
            <a href="https://www.datathistle.com/" target="_blank" rel="noopener noreferrer" title="Listings from Data Thistle">
              <img src={dataThistleLogo} alt="Data Thistle" className="h-7 rounded bg-[#1d1d1b] px-1.5 py-0.5" />
            </a>
            <a href="https://www.datathistle.com/update/event-listing/" target="_blank" rel="noopener noreferrer" className="underline">
              Add or update an event
            </a>
          </span>
        )}
      </p>
    </div>
  )
}

const STATUS_LABEL: Record<string, string> = {
  cancelled: 'Cancelled',
  postponed: 'Postponed',
  rescheduled: 'Rescheduled',
  movedOnline: 'Moved online',
}

function ShowRow({ show, serverId }: { show: LiveShow; serverId: number | null }) {
  const day = localDate(show.start_date)
  const yours = new Set(show.artists.map((a) => a.name))
  const others = show.performers.filter((p) => !yours.has(p.name))
  const where = [show.venue, show.city, show.country ? countryName(show.country) : null].filter(Boolean).join(' · ')
  const price = formatPrice(show.price_min, show.price_max, show.currency)
  const status = STATUS_LABEL[show.status]
  const [first, ...more] = show.tickets
  // Other sources' links for the same show (one each).
  const extra = more.filter((t, i) => t.source !== first?.source && more.findIndex((o) => o.source === t.source) === i)
  const star = useStarShow()
  const starred = star.isPending ? star.variables.starred : show.starred
  const ticket = useGotTicket()
  const gotTicket = starred && (ticket.isPending ? ticket.variables.gotTicket : show.got_ticket)
  return (
    <li className="flex items-start gap-3 p-3">
      <div className="w-12 shrink-0 text-center">
        <div className="text-xs uppercase text-muted">{day.toLocaleDateString(undefined, { weekday: 'short' })}</div>
        <div className="text-xl font-bold tabular-nums leading-tight">{day.getDate()}</div>
        <div className="text-xs text-muted">{day.toLocaleDateString(undefined, { month: 'short' })}</div>
      </div>
      <div className="min-w-0 flex-1 space-y-0.5 text-sm">
        <p className="font-medium">
          {show.artists.length === 0 && <span>{show.name}</span>}
          {show.artists.map((a, i) => (
            <span key={a.name}>
              {i > 0 && ', '}
              {a.server_id === serverId && a.rating_key != null ? (
                <Link to={`/library/artists/${a.rating_key}`} className={a.headliner ? 'font-semibold hover:underline' : 'font-normal hover:underline'}>
                  {a.name}
                </Link>
              ) : (
                <span className={a.headliner ? 'font-semibold' : 'font-normal'}>{a.name}</span>
              )}
            </span>
          ))}
          {status && <span className="ml-2 rounded-full bg-warn/15 px-2 py-0.5 text-xs font-medium text-warn">{status}</span>}
          {gotTicket && <span className="ml-2 rounded-full bg-ok/15 px-2 py-0.5 text-xs font-medium text-ok">🎟 Got a ticket</span>}
        </p>
        {(show.festival || others.length > 0) && (
          <p className="truncate text-muted">
            {show.festival ? show.name : null}
            {show.festival && others.length > 0 ? ' · ' : null}
            {others.length > 0 &&
              `with ${others
                .slice(0, 3)
                .map((p) => p.name)
                .join(', ')}${others.length > 3 ? ` and ${others.length - 3} more` : ''}`}
          </p>
        )}
        <p className="text-muted">
          {where}
          {show.start_time && ` · ${show.start_time}`}
          {show.end_date && ` · until ${localDate(show.end_date).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}`}
        </p>
        <p className="flex flex-wrap gap-x-3 text-xs text-muted">
          {show.distance_km != null && <span>{Math.round(show.distance_km)} km away</span>}
          {price && <span className="font-medium text-text">{price}</span>}
          {extra.map((t) => (
            <a key={t.url} href={t.url} target="_blank" rel="nofollow noopener noreferrer" className="underline hover:text-accent">
              {t.source === 'calendar' ? 'In your calendar link' : `Also on ${SOURCE_NAMES[t.source] ?? t.source}`}
            </a>
          ))}
          {starred && (
            <button
              type="button"
              aria-pressed={gotTicket}
              onClick={() => ticket.mutate({ ids: show.ids, gotTicket: !gotTicket })}
              className="underline hover:text-accent"
            >
              {gotTicket ? "I don't have a ticket" : "I've got a ticket"}
            </button>
          )}
          {starred && (
            <a href={`/api/live/shows/${show.id}.ics`} download className="underline hover:text-accent">
              Add to calendar
            </a>
          )}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          aria-pressed={starred}
          aria-label={starred ? 'Starred' : 'Star'}
          title={starred ? 'Starred: kept and in your calendar' : 'Star: keep it and put it in your calendar'}
          onClick={() => star.mutate({ ids: show.ids, starred: !starred })}
          className={`rounded-lg px-2 py-1 text-lg leading-none ${starred ? 'text-accent' : 'text-muted hover:text-text'}`}
        >
          {starred ? '★' : '☆'}
        </button>
        {first && (
          <a
            href={first.url}
            target="_blank"
            rel="nofollow noopener noreferrer"
            className="rounded-lg bg-surface-2 px-3 py-1.5 text-sm font-medium hover:text-accent"
            title={first.seller ? `Tickets from ${first.seller}` : undefined}
          >
            {first.is_ticket ? 'Tickets' : 'Details'}
          </a>
        )}
      </div>
    </li>
  )
}

/** The private calendar of starred shows, to subscribe to in a calendar app. */
function CalendarCard() {
  const [open, setOpen] = useState(false)
  const address = useCalendarAddress(open)
  const reset = useResetCalendar()
  const [copied, setCopied] = useState(false)
  const url = address.data?.url
  return (
    <Card title="Your calendar">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Star shows (☆) to keep them and add them to a calendar you subscribe to once in Apple Calendar, Google Calendar
          or Outlook. It stays up to date by itself.
        </p>
        {!open ? (
          <Button onClick={() => setOpen(true)}>Show calendar address</Button>
        ) : address.isPending ? (
          <Spinner small />
        ) : address.isError ? (
          <Notice tone="error">{errorMessage(address.error)}</Notice>
        ) : (
          url && (
            <div className="space-y-2">
              <input
                readOnly
                value={url}
                aria-label="Calendar address"
                onFocus={(e) => e.currentTarget.select()}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-xs"
              />
              <div className="flex flex-wrap gap-2">
                <a href={url.replace(/^https?:/, 'webcal:')} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-on-accent">
                  Subscribe
                </a>
                <Button
                  onClick={() =>
                    navigator.clipboard?.writeText(url).then(
                      () => setCopied(true),
                      () => setCopied(false),
                    )
                  }
                >
                  {copied ? 'Copied' : 'Copy'}
                </Button>
                <Button
                  variant="ghost"
                  busy={reset.isPending}
                  onClick={() => window.confirm('Make a new address? The old one stops working.') && reset.mutate()}
                >
                  New address
                </Button>
              </div>
              <p className="text-xs text-muted">
                Anyone with this address can see your starred shows. Google Calendar: Other calendars → From URL.
              </p>
            </div>
          )
        )}
      </div>
    </Card>
  )
}

/** Calendar links (iCal) whose shows are listed too: Songkick's calendar export, or any. */
function FeedsCard() {
  const feeds = useLiveFeeds()
  const add = useAddLiveFeed()
  const remove = useRemoveLiveFeed()
  const [url, setUrl] = useState('')
  const valid = /^(https?|webcal):\/\/\S+$/i.test(url.trim())
  return (
    <Card title="Calendar links">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Add a calendar link to list its shows here too, such as your Songkick calendar (its export link, for the artists
          you track or your plans). It's read along with the other sources.
        </p>
        {feeds.isPending ? (
          <Spinner small />
        ) : feeds.isError ? (
          <Notice tone="error">{errorMessage(feeds.error)}</Notice>
        ) : (
          feeds.data.length > 0 && (
            <ul className="space-y-2" aria-label="Calendar links">
              {feeds.data.map((f) => (
                <li key={f.id} className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{f.name ?? f.host}</p>
                    <p className="text-xs text-muted">
                      {f.error
                        ? f.error
                        : f.fetched_at
                          ? `${f.show_count ?? 0} upcoming · read ${formatRelative(f.fetched_at)}`
                          : 'Not read yet'}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    busy={remove.isPending && remove.variables === f.id}
                    onClick={() => remove.mutate(f.id)}
                    aria-label={`Remove ${f.name ?? f.host}`}
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )
        )}
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid) add.mutate(url.trim(), { onSuccess: () => setUrl('') })
          }}
        >
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="webcal://… or https://….ics"
            aria-label="Calendar link"
            className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
          />
          <Button type="submit" disabled={!valid} busy={add.isPending}>
            Add link
          </Button>
        </form>
        {add.isError && <Notice tone="error">{errorMessage(add.error)}</Notice>}
      </div>
    </Card>
  )
}
