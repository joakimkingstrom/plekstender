import { Link } from 'react-router'
import type { ArtworkJob } from '../api/artwork'
import { isRunning } from '../api/artworkJobStatus'

type Kind = ArtworkJob['kind']

const KINDS: Record<Kind, { running: string; done: string; page: string }> = {
  covers: { running: 'Applying best album covers', done: 'Best album covers applied', page: '/library/artwork/albums' },
  fanart: { running: 'Applying fanart.tv logos', done: 'fanart.tv logos applied', page: '/library/artwork' },
  export: { running: 'Exporting artist logos', done: 'Artist logos exported', page: '/settings/export/logos' },
  import: { running: 'Importing artist logos', done: 'Artist logos imported', page: '/settings/export/logos' },
}

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

/** The job in the bell's list: what it's doing and how far it's got, then what it did. */
export function ArtworkJobItem({ job, onClose, onNavigate }: { job: ArtworkJob; onClose: () => void; onNavigate: () => void }) {
  const kind = KINDS[job.kind]
  const running = isRunning(job)
  const { done = 0, total = 0 } = (job.progress ?? {}) as { done?: number; total?: number }
  return (
    <li className="flex gap-2 px-2 py-2.5 text-sm" aria-live="polite">
      <span
        className={`mt-1.5 size-2 shrink-0 rounded-full ${running ? 'animate-pulse bg-accent' : job.status === 'failed' ? 'bg-danger' : 'bg-ok'}`}
        aria-hidden
      />
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
            <p className="font-medium">{kind.running} failed</p>
            {job.error && <p className="text-xs text-muted">{job.error}</p>}
          </>
        ) : (
          <>
            <p className="font-medium">{kind.done}</p>
            <p className="text-xs text-muted">{summary(job)}</p>
          </>
        )}
        <p className="text-xs">
          {job.download && !running ? (
            <a href={job.download} className="text-accent hover:underline">
              Download
            </a>
          ) : (
            <Link to={kind.page} onClick={onNavigate} className="text-accent hover:underline">
              {running ? 'Open' : 'See them'}
            </Link>
          )}
        </p>
      </div>
      {!running && (
        <button
          type="button"
          onClick={onClose}
          aria-label={`Dismiss: ${kind.done}`}
          className="self-start rounded px-1 text-muted hover:bg-surface-2 hover:text-text"
        >
          ×
        </button>
      )}
    </li>
  )
}
