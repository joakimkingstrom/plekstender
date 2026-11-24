import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { bulk, useLyricsPlan, useRefreshFiles, type LyricsDestinations } from '../api/files'
import { formatCount } from '../format'
import { Dialog } from './Dialog'
import { Button, Notice, Spinner } from './ui'

const DESTINATIONS: [keyof LyricsDestinations, string, string][] = [
  ['sidecar', 'Next to each song as .lrc or .txt', 'Synced lyrics as .lrc, plain ones as .txt. Plex and Plexamp read these.'],
  [
    'lyricsfile',
    'Next to each song as a Lyricsfile',
    '.lyricsfile.yaml, timed word by word when LRCLIB has that. LRCGET and some players read these.',
  ],
  ['embedded', "In each song's tags", 'The lyrics as plain text, inside the file.'],
]

type Progress = { done?: number; total?: number; looked_up?: number; written?: number }

function songs(n: number) {
  return `${formatCount(n)} ${n === 1 ? 'song' : 'songs'}`
}

const history = (
  <Link to="/settings/files" className="underline">
    Settings → Music files
  </Link>
)

/**
 * Lyrics for some albums or the whole library from LRCLIB. Either written as they're found
 * (one edit, written along the way: a long lookup that stops keeps what it wrote), or looked
 * up first and written afterwards. Both can be undone. `jobId` continues an earlier lookup.
 */
export function LyricsPanel({
  serverId,
  albums,
  canWrite,
  jobId: startJob = null,
}: {
  serverId: number
  /** Album keys; empty for the whole library. */
  albums: number[]
  canWrite: boolean
  jobId?: number | null
}) {
  const [again, setAgain] = useState(false)
  const [asFound, setAsFound] = useState(canWrite)
  const [jobId, setJobId] = useState<number | null>(startJob)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [to, setTo] = useState<LyricsDestinations>({ sidecar: true, lyricsfile: false, embedded: false })
  const [overwrite, setOverwrite] = useState(false)
  const [written, setWritten] = useState<Schemas['TagChangeSetOut'] | null>(null)
  const [writing, setWriting] = useState(false)
  const plan = useLyricsPlan(serverId, jobId, to, overwrite)
  const nowhere = !to.sidecar && !to.lyricsfile && !to.embedded

  const start = async () => {
    setStarting(true)
    setError(null)
    setWritten(null)
    try {
      const write = asFound && canWrite ? { ...to, overwrite } : null
      setJobId((await bulk.lookUpLyrics(serverId, albums, again, write)).id)
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
      setWritten(await bulk.writeLyrics(serverId, jobId!, to, overwrite))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setWriting(false)
    }
  }

  const p = plan.data
  const busy = p ? p.job.status === 'queued' || p.job.status === 'running' : jobId !== null
  const progress = p?.job.progress as Progress | undefined
  // Written along the way (this lookup made its own edit), or afterwards from here.
  const wroteAsFound = Boolean(p?.applied_set_id) && !written

  const destinations = (
    <div className="space-y-3">
      <fieldset className="space-y-2">
        <legend className="mb-1 font-medium">Where to write lyrics</legend>
        {DESTINATIONS.map(([key, label, hint]) => (
          <label key={key} className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={to[key]}
              onChange={(e) => setTo((t) => ({ ...t, [key]: e.target.checked }))}
            />
            <span>
              {label}
              <span className="block text-xs text-muted">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
        Replace lyrics songs already have
      </label>
    </div>
  )
  const lookUp = (
    <div className="space-y-3">
      <label className={`flex items-start gap-2 ${canWrite ? '' : 'text-muted'}`}>
        <input
          type="checkbox"
          className="mt-0.5"
          disabled={!canWrite}
          checked={asFound && canWrite}
          onChange={(e) => setAsFound(e.target.checked)}
        />
        <span>
          Write lyrics as they're found
          <span className="block text-xs text-muted">
            One edit, written 50 songs at a time while the lookup runs: if it stops, what it found is written already.
            Songs looked up before are written too, without asking LRCLIB again.
          </span>
        </span>
      </label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={again} onChange={(e) => setAgain(e.target.checked)} />
        Look again for songs looked up before
      </label>
      <Button
        variant={jobId === null ? 'primary' : 'secondary'}
        busy={starting}
        disabled={asFound && canWrite && nowhere}
        onClick={() => void start()}
      >
        {jobId === null ? 'Look up lyrics' : 'Look up again'}
      </Button>
    </div>
  )
  const writeOff = !canWrite && (
    <Notice tone="warn">Writing to the files is off. Turn on tag editing on {history}.</Notice>
  )

  return (
    <div className="space-y-4 text-sm">
      {error && <Notice tone="error">{error}</Notice>}
      {jobId === null ? (
        <>
          {destinations}
          {writeOff}
          {lookUp}
        </>
      ) : !p ? (
        <Spinner />
      ) : (
        <>
          {busy ? (
            <div>
              <p className="mb-1.5">
                {p.applied_set_id ? 'Looking up and writing lyrics…' : 'Looking up lyrics…'}{' '}
                {progress?.total ? `${formatCount(progress.done ?? 0)} of ${songs(progress.total)}` : ''}
                {p.applied_set_id && progress?.written ? ` · ${formatCount(progress.written)} files written` : ''}
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
                ['Word-synced', p.word_synced],
                ['Synced by line', p.synced - p.word_synced],
                ['Plain only', p.plain],
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
            {p.instrumental > 0 && `; ${formatCount(p.instrumental)} instrumental`}
            {p.had_lyrics > 0 && `; ${formatCount(p.had_lyrics)} already had lyrics`}
            {p.untagged > 0 && `; ${formatCount(p.untagged)} have no artist or title to look up`}.
          </p>

          {wroteAsFound ? (
            <Notice>
              {busy ? 'Lyrics are written as they\'re found' : `Written as they were found: ${formatCount(progress?.written ?? 0)} files`}
              , as one edit. Undo it from the edit history on {history}.
            </Notice>
          ) : written ? (
            <Notice>
              Writing lyrics to {songs(p.songs)}. Undo it from the edit history on {history}.
            </Notice>
          ) : (
            <div className="space-y-3 border-t border-border pt-4">
              {destinations}
              <p>
                {nowhere
                  ? 'Choose where to write them.'
                  : p.songs === 0
                    ? busy
                      ? 'Nothing to write yet.'
                      : 'Nothing to write: the songs have these lyrics already, or none were found.'
                    : `Writing changes ${songs(p.songs)}: ${[
                        p.lrc && `${formatCount(p.lrc)} .lrc`,
                        p.txt && `${formatCount(p.txt)} .txt`,
                        p.lyricsfile && `${formatCount(p.lyricsfile)} Lyricsfiles`,
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
                        {[s.lrc && '.lrc', s.txt && '.txt', s.lyricsfile && 'Lyricsfile', s.lyrics && 'tags'].filter(Boolean).join(' + ')}
                        {s.word_synced && ' · word-synced'}
                        {s.had_lyrics && ' · replaces lyrics it had'}
                        {s.preview && ` · ${s.preview.split('\n')[0]}`}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
              {writeOff}
              <Button
                variant="primary"
                disabled={!canWrite || busy || nowhere || p.songs === 0}
                busy={writing}
                onClick={() => void write()}
              >
                Write lyrics to {songs(p.songs)}
              </Button>
            </div>
          )}
          {!busy && (
            <div className="space-y-3 border-t border-border pt-4">
              {(wroteAsFound || written) && destinations}
              {lookUp}
            </div>
          )}
        </>
      )}
    </div>
  )
}

/** The lyrics panel for some albums (Library → Tag editing). */
export function LyricsDialog({
  serverId,
  albums,
  canWrite,
  onClose,
}: {
  serverId: number
  albums: number[]
  canWrite: boolean
  onClose: () => void
}) {
  const refresh = useRefreshFiles(serverId)
  const what = albums.length ? `${formatCount(albums.length)} ${albums.length === 1 ? 'album' : 'albums'}` : 'the whole library'
  return (
    <Dialog
      title="Lyrics"
      subtitle={`From LRCLIB, for ${what}. Nothing is written until you say so, and the edit can be undone.`}
      onClose={() => {
        void refresh()
        onClose()
      }}
    >
      <LyricsPanel serverId={serverId} albums={albums} canWrite={canWrite} />
    </Dialog>
  )
}
