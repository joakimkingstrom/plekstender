import { Link } from 'react-router'
import type { Schemas } from '../api/client'
import { isRunning } from '../api/artworkJobStatus'
import { formatCount } from '../format'

type Progress = { done?: number; total?: number; looked_up?: number; written?: number }

/** A lyrics lookup in the bell's list: how far it's got, then what it did. */
export function LyricsJobItem({
  job,
  onClose,
  onNavigate,
}: {
  job: Schemas['FileScanOut']
  onClose: () => void
  onNavigate: () => void
}) {
  const running = isRunning(job)
  const { done = 0, total = 0, looked_up = 0, written = 0 } = (job.progress ?? {}) as Progress
  const did = [looked_up && `${formatCount(looked_up)} looked up`, written && `${formatCount(written)} files written`]
    .filter(Boolean)
    .join(', ')
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
              Looking up lyrics…{' '}
              <span className="font-normal tabular-nums text-muted">
                {formatCount(done)} / {total ? formatCount(total) : '?'}
              </span>
            </p>
            <div
              className="h-1.5 overflow-hidden rounded-full bg-surface-2"
              role="progressbar"
              aria-label="Looking up lyrics"
              aria-valuemin={0}
              aria-valuemax={total || undefined}
              aria-valuenow={total ? done : undefined}
            >
              <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${total ? (done / total) * 100 : 5}%` }} />
            </div>
            {written > 0 && <p className="text-xs text-muted">{formatCount(written)} files written so far</p>}
          </>
        ) : job.status === 'failed' ? (
          <>
            <p className="font-medium">Lyrics lookup stopped</p>
            {job.error && <p className="text-xs text-muted">{job.error}</p>}
          </>
        ) : (
          <>
            <p className="font-medium">Lyrics looked up</p>
            <p className="text-xs text-muted">{did || 'Nothing new to look up.'}</p>
          </>
        )}
        <p className="text-xs">
          <Link to="/library/lyrics" onClick={onNavigate} className="text-accent hover:underline">
            {running ? 'Open' : 'See them'}
          </Link>
        </p>
      </div>
      {!running && (
        <button
          type="button"
          onClick={onClose}
          aria-label="Dismiss: lyrics lookup"
          className="self-start rounded px-1 text-muted hover:bg-surface-2 hover:text-text"
        >
          ×
        </button>
      )}
    </li>
  )
}
