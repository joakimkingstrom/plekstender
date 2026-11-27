import { useState, type ReactNode } from 'react'
import { errorMessage } from '../api/client'
import { useStopJob, type StoppableJob } from '../api/jobs'
import { Dialog } from './Dialog'
import { Button, Spinner } from './ui'

/** Stop and Cancel… for a running job. Stop keeps what the job has done; Cancel, after a
 * confirmation saying what it takes back, undoes it where it can. Without `cancel`, only
 * Stop (when there's nothing to take back, or it can't be). */
export function JobStop({
  kind,
  jobId,
  requested,
  what,
  keeps,
  cancel,
  compact = false,
}: {
  kind: StoppableJob
  jobId: number
  /** What the job's been asked already (its `stop_requested`). */
  requested?: 'stop' | 'cancel' | null
  /** The job, as in "Cancel the scan?": "the scan". */
  what: string
  /** What Stop keeps, in a line: "Stop keeps the covers saved so far." */
  keeps?: string
  /** What Cancel takes back, for the confirmation. */
  cancel?: ReactNode
  /** Small, without the line about what Stop keeps (the bell's list). */
  compact?: boolean
}) {
  const stop = useStopJob(kind, jobId)
  const [confirming, setConfirming] = useState(false)
  const [sentFor, setSentFor] = useState<number | null>(null)
  // Asked (or being asked): it says so until the job ends, which takes this away. (Only
  // for this job: the next one shown here starts afresh.)
  const sent = sentFor === jobId && (stop.isPending || stop.isSuccess)
  const asked = sent ? (stop.variables ? 'cancel' : 'stop') : requested
  if (asked) {
    return (
      <span className="flex items-center gap-2 text-xs text-muted" aria-live="polite">
        <Spinner small /> {asked === 'cancel' ? 'Cancelling…' : 'Stopping…'}
      </span>
    )
  }
  const run = (withCancel: boolean) => {
    setConfirming(false)
    setSentFor(jobId)
    stop.mutate(withCancel)
  }
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <Button size={compact ? 'xs' : 'sm'} onClick={() => run(false)} aria-label={`Stop ${what}`}>
          Stop
        </Button>
        {cancel && (
          <Button size={compact ? 'xs' : 'sm'} variant="ghost" onClick={() => setConfirming(true)} aria-label={`Cancel ${what}…`}>
            Cancel…
          </Button>
        )}
        {!compact && keeps && <span className="text-xs text-muted">{keeps}</span>}
      </div>
      {stop.isError && sentFor === jobId && (
        <p className="text-xs text-danger" role="alert">
          {errorMessage(stop.error)}
        </p>
      )}
      {confirming && (
        <Dialog title={`Cancel ${what}?`} onClose={() => setConfirming(false)}>
          <div className="space-y-4 text-sm">
            <div className="space-y-2">{cancel}</div>
            <div className="flex flex-wrap gap-2">
              <Button variant="danger" onClick={() => run(true)}>
                Cancel {what}
              </Button>
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                Keep it running
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  )
}
