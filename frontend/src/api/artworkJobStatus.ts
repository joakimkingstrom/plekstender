import { useState } from 'react'
import { useArtworkJobs, type ArtworkJob } from './artwork'
import { useLatestLyricsLookup } from './files'
import { useActiveServer } from './library'

type Job = { id: number; status: string }

export const isRunning = (job: Job) => job.status === 'queued' || job.status === 'running'

/**
 * A background job for the bell: while it runs, and once it's done if it was seen running
 * during this visit, until closed. `unread`: done, and the bell's list hasn't been opened
 * since.
 */
function useBellJob<T extends Job>(newest: T | undefined) {
  const [seen, setSeen] = useState<Set<number>>(() => new Set())
  const [closed, setClosed] = useState<Set<number>>(() => new Set())
  const [read, setRead] = useState<Set<number>>(() => new Set())
  const running = newest ? isRunning(newest) : false
  // Noted while rendering (React's way to keep state in step with data), not in an effect.
  if (newest && running && !seen.has(newest.id)) setSeen(new Set(seen).add(newest.id))
  const job = newest && !closed.has(newest.id) && (running || seen.has(newest.id)) ? newest : null
  return {
    job,
    running: Boolean(job && running),
    unread: Boolean(job && !running && !read.has(job.id)),
    markRead: () => job && !running && !read.has(job.id) && setRead(new Set(read).add(job.id)),
    close: () => job && setClosed(new Set(closed).add(job.id)),
  }
}

/** The artwork job running on the active server (for its owner). */
export function useArtworkJobStatus() {
  const { active } = useActiveServer()
  const owner = active?.is_owner ? active : null
  const jobs = useArtworkJobs(owner?.id ?? 0, Boolean(owner))
  return useBellJob<ArtworkJob>(jobs.data?.[0])
}

/** The lyrics lookup running on the active server (for its owner). */
export function useLyricsJobStatus() {
  const { active } = useActiveServer()
  const owner = active?.is_owner ? active : null
  const latest = useLatestLyricsLookup(owner?.id ?? 0, 'any', Boolean(owner))
  return useBellJob(latest.data ?? undefined)
}
