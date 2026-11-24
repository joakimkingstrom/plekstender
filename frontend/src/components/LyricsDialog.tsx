import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { bulk, useLyricsPlan, useRefreshFiles, type LyricsPlacement } from '../api/files'
import { formatCount } from '../format'
import { Dialog } from './Dialog'
import { Segmented } from './Segmented'
import { Button, Notice, Spinner } from './ui'

const PLACEMENTS: [LyricsPlacement, string][] = [
  ['sidecar', 'Next to the songs'],
  ['embedded', 'In the tags'],
  ['both', 'Both'],
]

function songs(n: number) {
  return `${formatCount(n)} ${n === 1 ? 'song' : 'songs'}`
}

/**
 * Lyrics for some albums or the whole library: looked up on LRCLIB (nothing written), then
 * written as one edit that can be undone.
 */
export function LyricsDialog({
  serverId,
  albums,
  canWrite,
  onClose,
}: {
  serverId: number
  /** Album keys; empty for the whole library. */
  albums: number[]
  canWrite: boolean
  onClose: () => void
}) {
  const [again, setAgain] = useState(false)
  const [jobId, setJobId] = useState<number | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [placement, setPlacement] = useState<LyricsPlacement>('sidecar')
  const [overwrite, setOverwrite] = useState(false)
  const [written, setWritten] = useState<Schemas['TagChangeSetOut'] | null>(null)
  const [writing, setWriting] = useState(false)
  const plan = useLyricsPlan(serverId, jobId, placement, overwrite)
  const refresh = useRefreshFiles(serverId)
  const what = albums.length ? `${formatCount(albums.length)} ${albums.length === 1 ? 'album' : 'albums'}` : 'the whole library'

  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      setJobId((await bulk.lookUpLyrics(serverId, albums, again)).id)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setStarting(false)
    }
  }
  const write = async () => {
    setWriting(true)
    setError(null)
    try {
      setWritten(await bulk.writeLyrics(serverId, jobId!, placement, overwrite))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setWriting(false)
    }
  }

  const p = plan.data
  const busy = p ? p.job.status === 'queued' || p.job.status === 'running' : jobId !== null
  const progress = p?.job.progress as { done?: number; total?: number } | undefined

  return (
    <Dialog
      title="Lyrics"
      subtitle={`From LRCLIB, for ${what}. Synced lyrics go in a .lrc file next to each song, plain ones in a .txt: Plex and Plexamp read both. Nothing is written until you say so, and the edit can be undone.`}
      onClose={() => {
        void refresh()
        onClose()
      }}
    >
      <div className="space-y-4 text-sm">
        {error && <Notice tone="error">{error}</Notice>}
        {jobId === null ? (
          <>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={again} onChange={(e) => setAgain(e.target.checked)} />
              Look again for songs looked up before
            </label>
            <Button variant="primary" busy={starting} onClick={() => void start()}>
              Look up lyrics
            </Button>
          </>
        ) : !p ? (
          <Spinner />
        ) : (
          <>
            {busy ? (
              <div>
                <p className="mb-1.5">
                  Looking up lyrics… {progress?.total ? `${formatCount(progress.done ?? 0)} of ${songs(progress.total)}` : ''}
                </p>
                <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
                  <div
                    className="h-full rounded-full bg-accent transition-[width]"
                    style={{ width: `${progress?.total ? ((progress.done ?? 0) / progress.total) * 100 : 5}%` }}
                  />
                </div>
              </div>
            ) : p.job.status === 'failed' ? (
              <Notice tone="warn">{p.job.error ?? 'The lookup stopped.'}</Notice>
            ) : null}
            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {(
                [
                  ['Synced', p.synced],
                  ['Plain only', p.plain],
                  ['Instrumental', p.instrumental],
                  ['Not found', p.none + p.untagged + p.errors],
                ] as const
              ).map(([label, n]) => (
                <div key={label} className="rounded-lg bg-surface-2 px-3 py-2">
                  <dt className="text-xs text-muted">{label}</dt>
                  <dd className="font-display text-lg font-bold tabular-nums">{formatCount(n)}</dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-muted">
              Of {songs(p.files)}
              {p.had_lyrics > 0 && `; ${formatCount(p.had_lyrics)} already had lyrics`}
              {p.untagged > 0 && `; ${formatCount(p.untagged)} have no artist or title to look up`}.
            </p>

            {written ? (
              <Notice>
                Writing lyrics to {songs(p.songs)}. Undo it from the edit history on{' '}
                <Link to="/settings/files" className="underline">
                  Settings → Music files
                </Link>
                .
              </Notice>
            ) : (
              <div className="space-y-3 border-t border-border pt-4">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-medium">Write them</span>
                  <Segmented label="Where to write lyrics" value={placement} onChange={setPlacement} options={PLACEMENTS} />
                </div>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
                  Replace lyrics songs already have
                </label>
                <p>
                  {p.songs === 0
                    ? busy
                      ? 'Nothing to write yet.'
                      : 'Nothing to write: the songs have these lyrics already, or none were found.'
                    : `Writing changes ${songs(p.songs)}: ${[
                        p.lrc && `${formatCount(p.lrc)} .lrc`,
                        p.txt && `${formatCount(p.txt)} .txt`,
                        p.lyrics && `${formatCount(p.lyrics)} in tags`,
                      ]
                        .filter(Boolean)
                        .join(', ')}.`}
                </p>
                {p.sample.length > 0 && (
                  <ul className="max-h-60 divide-y divide-border overflow-y-auto rounded-lg border border-border">
                    {p.sample.map((s) => (
                      <li key={s.path} className="px-3 py-2">
                        <p className="truncate text-xs font-medium">{s.path}</p>
                        <p className="truncate text-xs text-muted">
                          {[s.lrc && '.lrc', s.txt && '.txt', s.lyrics && 'tags'].filter(Boolean).join(' + ')}
                          {s.had_lyrics && ' · replaces lyrics it had'}
                          {s.preview && ` · ${s.preview.split('\n')[0]}`}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
                {!canWrite && (
                  <Notice tone="warn">
                    Writing to the files is off. Turn on tag editing on{' '}
                    <Link to="/settings/files" className="underline">
                      Settings → Music files
                    </Link>
                    .
                  </Notice>
                )}
                <Button variant="primary" disabled={!canWrite || busy || p.songs === 0} busy={writing} onClick={() => void write()}>
                  Write lyrics to {songs(p.songs)}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </Dialog>
  )
}
