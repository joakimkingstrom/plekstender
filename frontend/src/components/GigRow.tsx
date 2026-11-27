import { Link } from 'react-router'
import { useGotTicket, useStarShow, type LiveShow } from '../api/live'
import { formatRelative } from '../format'
import { formatPrice, localDate } from '../live'
import { countryName } from '../places'

const SOURCE_NAMES: Record<string, string> = {
  jambase: 'JamBase',
  ticketmaster: 'Ticketmaster',
  datathistle: 'Data Thistle',
  calendar: 'your calendar link',
}

const CALENDAR_SITES: [RegExp, string][] = [
  [/(^|\.)songkick\.com$/, 'Songkick'],
  [/(^|\.)bandsintown\.com$/, 'Bandsintown'],
]

/** A gig from someone's calendar link, named after the site its page is on ("Songkick"). */
function calendarSite(url: string): string {
  let host = ''
  try {
    host = new URL(url).hostname.toLowerCase()
  } catch {
    return 'Your calendar'
  }
  return CALENDAR_SITES.find(([re]) => re.test(host))?.[1] ?? (host.replace(/^www\./, '') || 'Your calendar')
}

const STATUS_LABEL: Record<string, string> = {
  cancelled: 'Cancelled',
  postponed: 'Postponed',
  rescheduled: 'Rescheduled',
  movedOnline: 'Moved online',
}

export function GigRow({
  show,
  serverId,
  ticketPill = true,
}: {
  show: LiveShow
  serverId: number | null
  /** The "Got a ticket" pill (not in a list of gigs you've got tickets for). */
  ticketPill?: boolean
}) {
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
          {gotTicket && ticketPill && <span className="ml-2 rounded-full bg-ok/15 px-2 py-0.5 text-xs font-medium text-ok">🎟 Got a ticket</span>}
          {show.is_new && (
            <span
              className="ml-2 rounded-full bg-accent/15 px-2 py-0.5 text-xs font-medium text-accent"
              title={show.first_seen_at ? `First found ${formatRelative(show.first_seen_at)}` : undefined}
            >
              New
            </span>
          )}
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
              {t.source === 'calendar' ? calendarSite(t.url) : `Also on ${SOURCE_NAMES[t.source] ?? t.source}`}
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
