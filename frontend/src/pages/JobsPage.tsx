import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useJobs, type Job } from '../api/jobs'
import { JobStop } from '../components/JobStop'
import { Badge, Card, Notice, Spinner } from '../components/ui'
import { formatCount, formatRelative } from '../format'

/** How a finished job ended, in words and colour. */
const ENDED: Record<string, [string, 'ok' | 'warn' | 'bad']> = {
  succeeded: ['Done', 'ok'],
  applied: ['Written', 'ok'],
  partial: ['Partly written', 'warn'],
  failed: ['Failed', 'bad'],
  dropped: ['Nothing to write', 'warn'],
  cancelled: ['Cancelled', 'warn'],
}

function duration(from: string | null | undefined, to: string | null | undefined): string | null {
  if (!from || !to) return null
  const seconds = Math.max(0, Math.round((new Date(to).getTime() - new Date(from).getTime()) / 1000))
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.round(seconds / 60)
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}

/** Where to see more of a job (its own page). */
function more(job: Job): { to: string; label: string } | null {
  if (job.kind === 'edits') return { to: '/settings/files/history', label: 'Tag history' }
  if (job.kind === 'files' && job.subkind === 'lyrics') return { to: '/library/lyrics', label: 'Lyrics' }
  if (job.kind === 'files' && job.subkind === 'identify') return { to: '/settings/files/unknown', label: 'Unknown files' }
  if (job.kind === 'files' && job.subkind === 'scan') return { to: '/settings/files/scan', label: 'Tags in your files' }
  if (job.kind === 'artwork') return { to: '/library/artwork', label: 'Artwork' }
  if (job.kind === 'sync') return { to: '/settings', label: 'Servers' }
  if (job.kind === 'imports') return { to: '/settings/listening', label: 'Listening history' }
  return null
}

function ActiveJob({ job }: { job: Job }) {
  const { done = 0, total = 0 } = (job.progress ?? {}) as { done?: number; total?: number }
  const running = job.status === 'running'
  const link = more(job)
  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 font-medium">{job.what}</p>
        <span className="text-xs tabular-nums text-muted">
          {running ? (total ? `${formatCount(done)} / ${formatCount(total)}` : 'Running') : `Waiting since ${formatRelative(job.created_at)}`}
        </span>
      </div>
      {running && (
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
          <div
            className={`h-full rounded-full bg-accent ${total ? '' : 'w-1/4 animate-pulse'}`}
            style={total ? { width: `${(done / total) * 100}%` } : undefined}
          />
        </div>
      )}
      {job.waiting_for && (
        <p className="text-xs text-muted">
          Waiting for: <span className="text-text">{job.waiting_for.what}</span>
          {job.waiting_for.since && ` (started ${formatRelative(job.waiting_for.since)})`}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        {job.stoppable && job.kind !== 'sync' && (
          <JobStop kind={job.kind as 'artwork' | 'files' | 'edits' | 'imports'} jobId={job.id} requested={job.stop_requested as 'stop' | 'cancel' | null} what={job.what.toLowerCase()} compact />
        )}
        {link && (
          <Link to={link.to} className="text-muted hover:text-text">
            {link.label} →
          </Link>
        )}
      </div>
    </li>
  )
}

function FinishedJob({ job }: { job: Job }) {
  const stopped = job.stop_requested && job.status === 'failed'
  const [label, tone] = stopped ? [job.stop_requested === 'cancel' ? 'Cancelled' : 'Stopped', 'warn' as const] : (ENDED[job.status] ?? [job.status, 'warn' as const])
  const took = duration(job.started_at, job.finished_at)
  return (
    <li className="flex items-start gap-3 py-2.5 text-sm">
      <div className="min-w-0 flex-1">
        <p className="truncate">{job.what}</p>
        <p className="text-xs text-muted">
          {job.finished_at ? formatRelative(job.finished_at) : formatRelative(job.created_at)}
          {took && ` · took ${took}`}
        </p>
        {job.error && !stopped && <p className="mt-0.5 text-xs text-danger">{job.error}</p>}
      </div>
      <span
        className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
          tone === 'ok' ? 'bg-ok/15 text-ok' : tone === 'bad' ? 'bg-danger/15 text-danger' : 'bg-warn/15 text-warn'
        }`}
      >
        {label}
      </span>
    </li>
  )
}

/** Settings → Jobs: what the worker is doing and has done, for you and the servers you own. */
export function JobsPage() {
  const jobs = useJobs()
  if (jobs.isPending) return <Spinner />
  if (jobs.isError) return <Notice tone="error">{errorMessage(jobs.error)}</Notice>
  const { active, finished } = jobs.data
  return (
    <div className="space-y-4">
      <Card title="Running and waiting" action={active.length > 0 ? <Badge>{formatCount(active.length)}</Badge> : undefined}>
        {active.length === 0 ? (
          <p className="text-sm text-muted">Nothing's running or waiting.</p>
        ) : (
          <ul className="-my-3 divide-y divide-border">
            {active.map((job) => (
              <ActiveJob key={`${job.kind}-${job.id}`} job={job} />
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-muted">
          Jobs on the same music folder take turns (a scan, writing tags, renaming, finding duplicates), so one can wait for
          another; a waiting job says what for.
        </p>
      </Card>
      <Card title="Finished">
        {finished.length === 0 ? (
          <p className="text-sm text-muted">Nothing yet.</p>
        ) : (
          <ul className="-my-2.5 divide-y divide-border">
            {finished.map((job) => (
              <FinishedJob key={`${job.kind}-${job.id}`} job={job} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
