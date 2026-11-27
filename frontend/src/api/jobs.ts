import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

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

export type Job = Schemas['JobOut']

/** Settings → Jobs: running and waiting jobs, then the latest finished. Looked at every few
 * seconds while something's running or waiting. */
export function useJobs() {
  return useQuery({
    queryKey: ['jobs'],
    queryFn: () => unwrap(api.GET('/api/jobs')),
    refetchInterval: (query) => ((query.state.data?.active.length ?? 0) > 0 ? 3000 : 30_000),
  })
}
