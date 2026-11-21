import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'
import { useNotificationActions, useNotifications } from '../api/notifications'
import { formatRelative } from '../format'
import { useArtworkJobStatus } from '../api/artworkJobStatus'
import { ArtworkJobItem } from './ArtworkJobStatus'
import { Spinner } from './ui'

/** The bell in the header: a count of unread problems, and the list under it (with the
 * artwork job running on the server, and how far it's got). */
export function NotificationBell({ isAdmin }: { isAdmin: boolean }) {
  const notifications = useNotifications()
  const { read, dismiss, hide } = useNotificationActions()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const panel = useId()
  const job = useArtworkJobStatus()
  const items = notifications.data?.items ?? []
  const unread = (notifications.data?.unread ?? 0) + (job.unread ? 1 : 0)

  // Close on a click elsewhere or Escape.
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Opening the list reads what's in it.
  const toggle = () => {
    const next = !open
    setOpen(next)
    const fresh = items.filter((n) => !n.read && !n.resolved_at).map((n) => n.id)
    if (next && fresh.length) read.mutate(fresh)
    if (next) job.markRead()
  }

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={panel}
        aria-label={`Notifications${unread ? `: ${unread} new` : ''}${job.running ? ' (an artwork job is running)' : ''}`}
        className="relative rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text"
      >
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {job.running && unread === 0 && (
          <span className="absolute right-0.5 top-0.5 size-2 animate-pulse rounded-full bg-accent" aria-hidden />
        )}
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold leading-4 text-white tabular-nums">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          id={panel}
          className="absolute right-0 top-full z-30 mt-2 max-h-[70dvh] w-[min(22rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-border bg-surface p-2 shadow-xl"
        >
          <p className="px-2 pb-1 pt-1 text-xs font-semibold uppercase tracking-wider text-muted">Notifications</p>
          {notifications.isPending ? (
            <Spinner small />
          ) : items.length === 0 && !job.job ? (
            <p className="px-2 py-3 text-sm text-muted">Nothing needs a look. Everything's working.</p>
          ) : (
            <ul className="divide-y divide-border">
              {job.job && <ArtworkJobItem job={job.job} onClose={job.close} onNavigate={() => setOpen(false)} />}
              {items.map((n) => {
                const fixed = Boolean(n.resolved_at)
                return (
                  <li key={n.id} className={`flex gap-2 px-2 py-2.5 text-sm ${fixed ? 'opacity-60' : ''}`}>
                    <span
                      className={`mt-1.5 size-2 shrink-0 rounded-full ${fixed ? 'bg-ok' : n.level === 'error' ? 'bg-danger' : 'bg-warn'}`}
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">
                        {n.title}
                        {fixed && <span className="ml-1 font-normal text-ok">· fixed</span>}
                      </p>
                      {n.detail && <p className="text-xs text-muted">{n.detail}</p>}
                      <p className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-muted">
                        <span>{formatRelative(fixed ? n.resolved_at! : n.updated_at)}</span>
                        {n.instance && <span>For admins</span>}
                        {!fixed && n.link && (
                          <Link to={n.link} onClick={() => setOpen(false)} className="text-accent hover:underline">
                            Fix
                          </Link>
                        )}
                        {isAdmin && n.instance && !fixed && (
                          <button type="button" onClick={() => hide.mutate(n.id)} className="hover:text-text">
                            Hide for all admins
                          </button>
                        )}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => dismiss.mutate(n.id)}
                      aria-label={`Dismiss: ${n.title}`}
                      className="self-start rounded px-1 text-muted hover:bg-surface-2 hover:text-text"
                    >
                      ×
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
