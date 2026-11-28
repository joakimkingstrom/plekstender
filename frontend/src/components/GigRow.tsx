import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useGotTicket, useMakeGigPlaylist, useStarShow, type LiveShow } from '../api/live'
import { formatCount, formatRelative } from '../format'
import { formatPrice, localDate } from '../live'
import { countryName } from '../places'
import { Dialog } from './Dialog'
import { Tooltip } from './Tooltip'
import { Button } from './ui'

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
  const playlist = useMakeGigPlaylist()
  const [undoTicket, setUndoTicket] = useState(false)
  const gotTicket = starred && (ticket.isPending ? ticket.variables.gotTicket : show.got_ticket)
  return (
    // The buttons sit beside the name lines; the venue and the rest run on to the right edge
    // below them.
    <li className="grid grid-cols-[3rem_minmax(0,1fr)_auto] items-start gap-x-3 p-3">
      <div className="row-span-2 text-center">
        <div className="text-xs uppercase text-muted">{day.toLocaleDateString(undefined, { weekday: 'short' })}</div>
        <div className="text-xl font-bold tabular-nums leading-tight">{day.getDate()}</div>
        <div className="text-xs text-muted">{day.toLocaleDateString(undefined, { month: 'short' })}</div>
      </div>
      <div className="min-w-0 space-y-0.5 text-sm">
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
          {gotTicket && ticketPill && (
            <button
              type="button"
              onClick={() => setUndoTicket(true)}
              className="ml-2 rounded-full bg-ok/15 px-2 py-0.5 text-xs font-medium text-ok hover:bg-ok/25"
              title="Change it"
            >
              🎟 Got a ticket
            </button>
          )}
          {starred && !gotTicket && (
            <button
              type="button"
              onClick={() => ticket.mutate({ ids: show.ids, gotTicket: true })}
              className="ml-2 rounded-full border border-dashed border-border px-2 py-0.5 text-xs font-medium text-muted hover:border-ok hover:text-ok"
            >
              🎟 Got a ticket?
            </button>
          )}
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
                .join(', ')}${others.length > 3 ? `, and ${others.length - 3} more` : ''}`}
          </p>
        )}
      </div>
      <div className="col-start-2 col-span-2 row-start-2 mt-0.5 min-w-0 space-y-0.5 text-sm">
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
        </p>
        {playlist.isError && <p className="text-xs text-danger">{errorMessage(playlist.error)}</p>}
        {playlist.isSuccess && (
          <p className="text-xs text-ok">
            {playlist.data.playlist_id
              ? `Playlist ready: ${formatCount(playlist.data.songs)} songs${playlist.data.missing ? `, ${formatCount(playlist.data.missing)} not in your library` : ''}. `
              : 'None of the songs they play lately are in your library.'}
            {playlist.data.playlist_id ? (
              <Link to="/library/playlists" className="underline">
                Playlists
              </Link>
            ) : null}
          </p>
        )}
      </div>
      <div className="col-start-3 row-start-1 flex items-center gap-1">
        {starred && (
          <Tooltip content="Add to your calendar">
            <a
              href={`/api/live/shows/${show.id}.ics`}
              download
              aria-label="Add to calendar"
              className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text"
            >
              <CalendarIcon />
            </a>
          </Tooltip>
        )}
        {show.artists.length > 0 && (
          <Tooltip content={show.playlist_id ? "Rebuild the playlist of what they've been playing lately" : "Make a playlist of what they've been playing lately"}>
            <button
              type="button"
              onClick={() => playlist.mutate(show.id)}
              disabled={playlist.isPending}
              aria-label={show.playlist_id ? 'Rebuild the playlist for this gig' : 'Make a playlist for this gig'}
              className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text disabled:animate-pulse"
            >
              <PlaylistIcon />
            </button>
          </Tooltip>
        )}
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
      </div>
      {undoTicket && (
        <Dialog title="No ticket after all?" onClose={() => setUndoTicket(false)}>
          <div className="space-y-4 text-sm">
            <p>
              Change {show.artists[0]?.name ?? show.name} on {day.toLocaleDateString(undefined, { day: 'numeric', month: 'long' })} to not having a
              ticket?
            </p>
            <div className="flex gap-2">
              <Button
                variant="primary"
                busy={ticket.isPending}
                onClick={() => ticket.mutate({ ids: show.ids, gotTicket: false }, { onSuccess: () => setUndoTicket(false) })}
              >
                I don't have a ticket
              </Button>
              <Button variant="ghost" onClick={() => setUndoTicket(false)}>
                Keep it
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </li>
  )
}

function CalendarIcon() {
  return (
    <svg viewBox="0 0 16 16" className="size-[18px]" aria-hidden>
      <rect x="2.5" y="3.5" width="11" height="10" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M2.5 6.5h11M5.5 2v3M10.5 2v3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

function PlaylistIcon() {
  return (
    <svg viewBox="0 0 16 16" className="size-[18px]" aria-hidden>
      <path d="M2.5 4h7M2.5 7.5h7M2.5 11h4.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M12 12.5V5l2 1" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="10.6" cy="12.6" r="1.4" fill="currentColor" />
    </svg>
  )
}
