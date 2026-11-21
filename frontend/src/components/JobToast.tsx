import { useState } from 'react'
import { Link, useLocation } from 'react-router'
import { useArtworkJobs, type ArtworkJob } from '../api/artwork'
import { useActiveServer } from '../api/library'

type Kind = ArtworkJob['kind']

const KINDS: Record<Kind, { running: string; done: string; page: string }> = {
  covers: { running: 'Applying best album covers', done: 'Best album covers applied', page: '/library/artwork/albums' },
  fanart: { running: 'Applying fanart.tv logos', done: 'fanart.tv logos applied', page: '/library/artwork' },
  export: { running: 'Exporting artist logos', done: 'Artist logos exported', page: '/settings/export/logos' },
  import: { running: 'Importing artist logos', done: 'Artist logos imported', page: '/settings/export/logos' },
}

const isRunning = (job: ArtworkJob) => job.status === 'queued' || job.status === 'running'

/** What a finished job did, in a line. */
function summary(job: ArtworkJob): string {
  const s = (job.stats ?? {}) as Record<string, number>
  const parts = (pairs: [number | undefined, string][]) =>
    pairs
      .filter(([n]) => n)
      .map(([n, text]) => `${n} ${text}`)
      .join(', ')
  if (job.kind === 'covers')
    return parts([[s.applied, 'applied'], [s.not_found, 'without a match'], [s.already, 'already as big'], [s.failed, 'failed']]) || 'Nothing to do.'
  if (job.kind === 'fanart')
    return parts([[s.applied, 'applied'], [s.no_logo, 'without a logo'], [s.no_mbid, 'not on MusicBrainz'], [s.failed, 'failed']]) || 'Nothing to do.'
  if (job.kind === 'export') return `${s.exported ?? 0} exported.`
  return parts([[s.applied, 'applied'], [s.failed, 'failed']]) || 'Done.'
}

/**
 * A card in the corner, on every page, while an artwork job runs on the server (for its
 * owner): what it's doing and how far it's got, then what it did. Not shown on the page that
 * already shows the job.
 */
export function JobToast() {
  const { active } = useActiveServer()
  const owner = active?.is_owner ? active : null
  const jobs = useArtworkJobs(owner?.id ?? 0, Boolean(owner))
  const location = useLocation()
  // Finished jobs are only shown if they were seen running during this visit.
  const [seen, setSeen] = useState<Set<number>>(() => new Set())
  const [closed, setClosed] = useState<Set<number>>(() => new Set())
  const job = jobs.data?.[0]
  const running = job ? isRunning(job) : false

  // Noted while rendering (React's way to keep state in step with data), not in an effect.
  if (job && running && !seen.has(job.id)) setSeen(new Set(seen).add(job.id))

  if (!job || closed.has(job.id) || !(running || seen.has(job.id))) return null
  const kind = KINDS[job.kind]
  if (location.pathname === kind.page) return null
  const { done = 0, total = 0 } = (job.progress ?? {}) as { done?: number; total?: number }
  const close = () => setClosed((prev) => new Set(prev).add(job.id))

  return (
    <div
      role="status"
      aria-label="Artwork job"
      className="fixed right-4 bottom-[calc(env(safe-area-inset-bottom)+4.5rem)] z-20 w-[min(20rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-3 text-sm shadow-xl md:bottom-4"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1 space-y-1.5">
          {running ? (
            <>
              <p className="font-medium">
                {kind.running}… <span className="font-normal tabular-nums text-muted">{done} / {total || '?'}</span>
              </p>
              <div
                className="h-1.5 overflow-hidden rounded-full bg-surface-2"
                role="progressbar"
                aria-label={kind.running}
                aria-valuemin={0}
                aria-valuemax={total || undefined}
                aria-valuenow={total ? done : undefined}
              >
                <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${total ? (done / total) * 100 : 5}%` }} />
              </div>
            </>
          ) : job.status === 'failed' ? (
            <>
              <p className="font-medium text-danger">{kind.running} failed</p>
              {job.error && <p className="text-xs text-muted">{job.error}</p>}
            </>
          ) : (
            <>
              <p className="font-medium text-ok">✓ {kind.done}</p>
              <p className="text-xs text-muted">{summary(job)}</p>
            </>
          )}
          <p className="text-xs">
            {job.download && !running ? (
              <a href={job.download} className="text-accent hover:underline">
                Download
              </a>
            ) : (
              <Link to={kind.page} className="text-accent hover:underline">
                {running ? 'Open' : 'See them'}
              </Link>
            )}
          </p>
        </div>
        {!running && (
          <button type="button" onClick={close} aria-label="Close" className="rounded px-1 text-muted hover:bg-surface-2 hover:text-text">
            ×
          </button>
        )}
      </div>
    </div>
  )
}
