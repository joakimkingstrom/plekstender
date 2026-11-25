import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { bulk, useDuplicates, useRefreshFiles, type Duplicates } from '../api/files'
import { formatCount, formatDuration } from '../format'
import { Dialog } from './Dialog'
import { Badge, Button, Notice, Spinner } from './ui'

type Group = Schemas['DupeGroupOut']
const STALE_MS = 24 * 3600 * 1000

function megabytes(bytes: number) {
  return `${(bytes / 1_000_000).toFixed(1)} MB`
}

/** Per group: the file to keep, or null to leave the group alone. Confirmed groups start
 * with the suggested copy; ones not checked by sound are left alone unless picked. */
function defaults(groups: Group[]): (number | null)[] {
  return groups.map((g) => (g.confirmed ? (g.files.find((f) => f.keep)?.file_id ?? null) : null))
}

/** Library → Tag editing → Duplicate files: the same recording kept more than once. */
export function DuplicatesDialog({ serverId, canWrite, onClose }: { serverId: number; canWrite: boolean; onClose: () => void }) {
  const found = useDuplicates(serverId)
  const qc = useQueryClient()
  const refresh = useRefreshFiles(serverId)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [openedAt] = useState(() => Date.now())

  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      await bulk.findDuplicates(serverId)
      await qc.invalidateQueries({ queryKey: ['servers', serverId, 'files', 'duplicates'] })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setStarting(false)
    }
  }

  const d = found.data
  const running = d ? d.job.status === 'queued' || d.job.status === 'running' : false
  const stale = d?.job.finished_at ? openedAt - new Date(d.job.finished_at).getTime() > STALE_MS : false
  const progress = d?.job.progress as { done?: number; total?: number } | undefined

  return (
    <Dialog
      title="Duplicate files"
      subtitle="Songs kept more than once: the same recording (compared by sound), whatever the files are called. The copies you don't keep move to a hidden folder, .plekstender-duplicates, in the music folder, which Plex doesn't look in. Undo moves them back."
      onClose={() => {
        void refresh()
        onClose()
      }}
    >
      <div className="space-y-4 text-sm">
        {error && <Notice tone="error">{error}</Notice>}
        {found.isPending ? (
          <Spinner />
        ) : found.isError ? (
          <Notice tone="error">{errorMessage(found.error)}</Notice>
        ) : running ? (
          <div>
            <p className="mb-1.5">
              Comparing files by sound…{' '}
              {progress?.total ? `${formatCount(progress.done ?? 0)} of ${formatCount(progress.total)}` : ''}
            </p>
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
              <div
                className="h-full rounded-full bg-accent transition-[width]"
                style={{ width: `${progress?.total ? ((progress.done ?? 0) / progress.total) * 100 : 5}%` }}
              />
            </div>
          </div>
        ) : !d || stale || d.applied_set_id ? (
          <div className="space-y-2">
            {d && <p className="text-muted">{d.applied_set_id ? 'Those duplicates were moved.' : 'The last check is more than a day old.'}</p>}
            <Button variant="primary" busy={starting} onClick={() => void start()}>
              {d ? 'Check again' : 'Look for duplicates'}
            </Button>
          </div>
        ) : d.job.status === 'failed' ? (
          <>
            <Notice tone="error">{d.job.error ?? 'The check failed.'}</Notice>
            <Button busy={starting} onClick={() => void start()}>
              Try again
            </Button>
          </>
        ) : (
          <Results key={d.job.id} serverId={serverId} found={d} canWrite={canWrite} onAgain={() => void start()} again={starting} />
        )}
      </div>
    </Dialog>
  )
}

function Results({
  serverId,
  found,
  canWrite,
  onAgain,
  again,
}: {
  serverId: number
  found: Duplicates
  canWrite: boolean
  onAgain: () => void
  again: boolean
}) {
  const [keep, setKeep] = useState<(number | null)[]>(() => defaults(found.groups))
  const [moving, setMoving] = useState(false)
  const [moved, setMoved] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const remove = found.groups.flatMap((g, i) => (keep[i] === null ? [] : g.files.filter((f) => f.file_id !== keep[i]).map((f) => f.file_id)))
  const saved = found.groups.reduce(
    (n, g, i) => n + (keep[i] === null ? 0 : g.files.filter((f) => f.file_id !== keep[i]).reduce((m, f) => m + f.size, 0)),
    0,
  )

  const move = async () => {
    setMoving(true)
    setError(null)
    try {
      await bulk.removeDuplicates(serverId, found.job.id, remove)
      setMoved(remove.length)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setMoving(false)
    }
  }

  if (moved !== null) {
    return (
      <Notice>
        Moving {formatCount(moved)} {moved === 1 ? 'file' : 'files'} into .plekstender-duplicates. Delete that folder when
        you're sure, or undo from the edit history on{' '}
        <Link to="/settings/files" className="underline">
          Settings → Music files
        </Link>
        .
      </Notice>
    )
  }
  return (
    <>
      {found.fingerprint_error && (
        <Notice tone="warn">
          Files couldn't be compared by sound ({found.fingerprint_error}), so these only share a name and length: check
          before moving any.
        </Notice>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p>
          {found.total_groups === 0
            ? `No duplicates among ${formatCount(found.files)} files.`
            : `${formatCount(found.total_groups)} ${found.total_groups === 1 ? 'song is' : 'songs are'} here more than once.`}
          {found.groups.length < found.total_groups && ` The first ${formatCount(found.groups.length)} are shown.`}
        </p>
        <Button variant="ghost" size="xs" busy={again} onClick={onAgain}>
          Check again
        </Button>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <ul className="max-h-[55dvh] space-y-3 overflow-y-auto">
        {found.groups.map((g, i) => {
          const first = g.files[0]
          const name = `group-${i}`
          return (
            <li key={g.files.map((f) => f.file_id).join('-')} className="rounded-lg border border-border p-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <p className="font-medium">
                  {first.title ?? first.path}
                  {first.artist && <span className="font-normal text-muted"> · {first.artist}</span>}
                </p>
                {g.confirmed ? (
                  <Badge tone="ok">Same recording{g.similarity !== null && ` · ${Math.round(g.similarity * 100)}% alike`}</Badge>
                ) : (
                  <Badge>Same name and length; not compared by sound</Badge>
                )}
              </div>
              <fieldset className="space-y-1.5">
                <legend className="sr-only">Which copy of {first.title ?? first.path} to keep</legend>
                {g.files.map((f) => (
                  <label key={f.file_id} className="flex items-start gap-2">
                    <input
                      type="radio"
                      name={name}
                      className="mt-1"
                      checked={keep[i] === f.file_id}
                      onChange={() => setKeep((k) => k.map((v, j) => (j === i ? f.file_id : v)))}
                    />
                    <span className="min-w-0">
                      <span className="block truncate">
                        Keep <span className="font-medium">{f.path}</span>
                      </span>
                      <span className="block text-xs text-muted">
                        {[
                          f.format?.toUpperCase(),
                          f.kbps && `${f.kbps} kbps`,
                          megabytes(f.size),
                          f.duration_ms && formatDuration(f.duration_ms),
                          f.album,
                          f.rating_key !== null ? 'in Plex' : 'not in Plex',
                          f.sidecars.length > 0 && `with ${f.sidecars.length === 1 ? 'a lyrics file' : 'lyrics files'}`,
                          f.keep && 'suggested',
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </span>
                    </span>
                  </label>
                ))}
                <label className="flex items-center gap-2 text-muted">
                  <input
                    type="radio"
                    name={name}
                    checked={keep[i] === null}
                    onChange={() => setKeep((k) => k.map((v, j) => (j === i ? null : v)))}
                  />
                  Keep them all
                </label>
              </fieldset>
            </li>
          )
        })}
      </ul>
      {found.total_groups > 0 && (
        <div className="space-y-2 border-t border-border pt-3">
          {!canWrite && (
            <Notice tone="warn">
              Moving files is off. Turn on tag editing on{' '}
              <Link to="/settings/files" className="underline">
                Settings → Music files
              </Link>
              .
            </Notice>
          )}
          <Button variant="primary" disabled={!canWrite || remove.length === 0} busy={moving} onClick={() => void move()}>
            Move {formatCount(remove.length)} {remove.length === 1 ? 'copy' : 'copies'} out of the library
            {saved > 0 && ` (${megabytes(saved)})`}
          </Button>
        </div>
      )}
    </>
  )
}
