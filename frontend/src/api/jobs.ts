import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './client'

/** The jobs that can be stopped: artwork jobs, music folder jobs (scans, identifying,
 * duplicates), edits being written, and history imports and sends. */
export type StoppableJob = 'artwork' | 'files' | 'edits' | 'imports'

/** Stop a job (it keeps what it did), or cancel it (taking that back where it can). A running
 * job does it at its next item; the job's own query then shows how it ended. */
export function useStopJob(kind: StoppableJob, jobId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (cancel: boolean) =>
      unwrap(
        api.POST('/api/jobs/{kind}/{job_id}/stop', {
          params: { path: { kind, job_id: jobId } },
          body: { cancel },
        }),
      ),
    // Whichever page shows the job polls it; have them all look again now.
    onSuccess: () => qc.invalidateQueries(),
  })
}
