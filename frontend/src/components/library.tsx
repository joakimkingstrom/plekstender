import { useState } from 'react'
import type { Schemas } from '../api/client'
import { imageUrl, useStartSync, useSyncStatus } from '../api/library'
import { errorMessage } from '../api/client'
import { describeSchedule, formatCount, formatRelative } from '../format'
import { Button, Notice, Spinner } from './ui'

/** Square artwork from Plex, with a lettered placeholder when there's none (or it fails). */
export function Artwork({
  serverId,
  path,
  title,
  size = 256,
  round = false,
  className = '',
}: {
  serverId: number
  path: string | null | undefined
  title: string
  size?: number
  round?: boolean
  className?: string
}) {
  const [failed, setFailed] = useState(false)
  const src = imageUrl(serverId, path, size)
  const shape = round ? 'rounded-full' : 'rounded-lg'
  if (!src || failed) {
    return (
      <div
        className={`flex aspect-square items-center justify-center bg-surface-2 font-display text-2xl font-bold text-muted ${shape} ${className}`}
        aria-hidden
      >
        {title.slice(0, 1).toUpperCase()}
      </div>
    )
  }
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className={`aspect-square bg-surface-2 object-cover ${shape} ${className}`}
    />
  )
}

/** Read-only rating on Plex's 0-10 scale, shown as 5 stars with halves. */
export function Stars({ rating, className = '' }: { rating: number | null | undefined; className?: string }) {
  if (rating === null || rating === undefined) {
    return <span className={`text-xs text-muted ${className}`}>Unrated</span>
  }
  const stars = rating / 2
  return (
    <span
      className={`inline-flex items-center gap-px text-accent ${className}`}
      role="img"
      aria-label={`${stars} out of 5 stars`}
    >
      {[1, 2, 3, 4, 5].map((i) => {
        const fill = Math.max(0, Math.min(1, stars - (i - 1)))
        return (
          <svg key={i} viewBox="0 0 20 20" className="size-3.5" aria-hidden>
            <defs>
              <linearGradient id={`star-${i}-${fill}`}>
                <stop offset={`${fill * 100}%`} stopColor="currentColor" />
                <stop offset={`${fill * 100}%`} stopColor="currentColor" stopOpacity="0.2" />
              </linearGradient>
            </defs>
            <path
              fill={`url(#star-${i}-${fill})`}
              d="M10 1.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L10 14.9l-5.2 2.7 1-5.8L1.5 7.7l5.9-.9z"
            />
          </svg>
        )
      })}
    </span>
  )
}

const PHASES: Record<string, string> = {
  artists: 'Reading artists',
  albums: 'Reading albums',
  tracks: 'Reading tracks',
  artist_details: 'Fetching genres, moods and similar artists',
  album_details: 'Fetching album genres and moods',
  artist_activity: 'Checking recent plays and ratings',
  album_activity: 'Checking recent plays and ratings',
  track_activity: 'Checking recent plays and ratings',
}

function SyncProgress({ run }: { run: Schemas['SyncRunOut'] }) {
  if (run.status === 'queued') {
    return <p className="text-sm text-muted">Waiting for the background worker…</p>
  }
  const { phase, done, total } = run.progress as { phase?: string; done?: number; total?: number }
  const pct = done && total ? Math.min(100, Math.round((done / total) * 100)) : null
  return (
    <div className="space-y-2">
      <div className="flex justify-between gap-3 text-sm">
        <span>{(phase && PHASES[phase]) ?? 'Starting'}</span>
        {done !== undefined && total ? (
          <span className="text-muted tabular-nums">
            {formatCount(done)} / {formatCount(total)}
          </span>
        ) : null}
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div
          className={`h-full rounded-full bg-accent transition-[width] ${pct === null ? 'w-1/3 animate-pulse' : ''}`}
          style={pct === null ? undefined : { width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

/** Sync status for one server, with "sync now" controls. */
export function SyncPanel({ serverId, compact = false }: { serverId: number; compact?: boolean }) {
  const status = useSyncStatus(serverId)
  const start = useStartSync(serverId)

  if (status.isPending) return <Spinner small />
  if (status.isError) return <Notice tone="error">{errorMessage(status.error)}</Notice>
  const { active, last, last_sync_at, counts, incremental_minutes, full_days } = status.data

  return (
    <div className="space-y-4">
      {active ? (
        <SyncProgress run={active} />
      ) : (
        <p className="text-sm text-muted">
          {last_sync_at ? `Synced ${formatRelative(last_sync_at)}.` : 'Not synced yet.'}{' '}
          {!compact && describeSchedule(incremental_minutes, full_days)}
        </p>
      )}
      {!active && last?.status === 'failed' && (
        <Notice tone="error">Last sync failed: {last.error ?? 'unknown error'}</Notice>
      )}
      {counts.tracks > 0 && (
        <dl className="grid grid-cols-3 gap-3 text-center">
          {(
            [
              ['Artists', counts.artists],
              ['Albums', counts.albums],
              ['Tracks', counts.tracks],
            ] as const
          ).map(([label, value]) => (
            <div key={label} className="rounded-lg bg-surface-2 px-2 py-3">
              <dt className="text-xs text-muted">{label}</dt>
              <dd className="font-display text-lg font-bold tabular-nums">{formatCount(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {!active && (
        <div className="flex flex-wrap gap-2">
          <Button busy={start.isPending && start.variables === 'incremental'} onClick={() => start.mutate('incremental')}>
            Sync changes
          </Button>
          <Button
            variant="ghost"
            busy={start.isPending && start.variables === 'full'}
            onClick={() => start.mutate('full')}
            title="Re-read the whole library, including removals"
          >
            Full resync
          </Button>
        </div>
      )}
      {start.isError && <Notice tone="error">{errorMessage(start.error)}</Notice>}
    </div>
  )
}
