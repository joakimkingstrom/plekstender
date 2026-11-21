import { useState } from 'react'
import { useArtworkJobs, type ArtworkJob } from './artwork'
import { useActiveServer } from './library'

export const isRunning = (job: ArtworkJob) => job.status === 'queued' || job.status === 'running'

/**
 * The artwork job running on the active server (for its owner), for the bell: while it
 * runs, and once it's done if it was seen running during this visit, until closed.
 * `unread`: done, and the bell's list hasn't been opened since.
 */
export function useArtworkJobStatus() {
  const { active } = useActiveServer()
  const owner = active?.is_owner ? active : null
  const jobs = useArtworkJobs(owner?.id ?? 0, Boolean(owner))
  const [seen, setSeen] = useState<Set<number>>(() => new Set())
  const [closed, setClosed] = useState<Set<number>>(() => new Set())
  const [read, setRead] = useState<Set<number>>(() => new Set())
  const newest = jobs.data?.[0]
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
