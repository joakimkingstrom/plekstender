import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useState } from 'react'
import { useAlbumFiles, useFilesSummary, useSetTagEditing, useStartScan, type TrackFile } from '../api/files'
import { MusicBrainzLookup } from './MusicBrainzLookup'
import { PlexSync } from './PlexSync'
import { TagEditor, TagHistory } from './TagEditor'
import { formatCount, formatDuration, formatRelative } from '../format'
import { Tooltip } from './Tooltip'
import { Button, Card, Notice, Spinner } from './ui'

/** Scanning the music folder's tags (in the worker), with progress and the last result. */
export function ScanCard({ serverId }: { serverId: number }) {
  const summary = useFilesSummary(serverId)
  const start = useStartScan(serverId)
  const setEditing = useSetTagEditing(serverId)
  if (summary.isPending) return <Spinner />
  if (summary.isError) return <Notice tone="error">{errorMessage(summary.error)}</Notice>
  const s = summary.data
  const active = s.active
  const { done = 0, total = 0 } = (active?.progress ?? {}) as { done?: number; total?: number }
  return (
    <Card title="Tags in your files">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          The worker reads the tags of every music file, so album pages can show them next to Plex's. Nothing is
          changed in the files. Later scans only read new and changed files.
        </p>
        {!s.mapped ? (
          <Notice>Set up the music folder first.</Notice>
        ) : active ? (
          <div className="space-y-2" aria-live="polite">
            <p>
              {active.status === 'queued' ? 'Waiting for the worker…' : 'Scanning…'}{' '}
              {total > 0 && (
                <span className="text-muted tabular-nums">
                  {formatCount(done)} / {formatCount(total)}
                </span>
              )}
            </p>
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
              <div
                className={`h-full rounded-full bg-accent ${total ? '' : 'w-1/4 animate-pulse'}`}
                style={total ? { width: `${(done / total) * 100}%` } : undefined}
              />
            </div>
          </div>
        ) : (
          <>
            {s.files > 0 && (
              <p>
                {formatCount(s.files)} files, {formatCount(s.matched)} of them Plex tracks
                {s.errors > 0 && <span className="text-danger">; {formatCount(s.errors)} couldn't be read</span>}.
              </p>
            )}
            {s.last?.status === 'failed' && <Notice tone="error">The last scan stopped: {s.last.error}</Notice>}
            {s.last?.status === 'succeeded' && s.last.finished_at && (
              <p className="text-xs text-muted">Last scanned {formatRelative(s.last.finished_at)}.</p>
            )}
            <Button variant="primary" busy={start.isPending} onClick={() => start.mutate()}>
              {s.files ? 'Scan again' : 'Scan the music folder'}
            </Button>
          </>
        )}
        {start.isError && <Notice tone="error">{errorMessage(start.error)}</Notice>}
        {s.mapped && (
          <div className="space-y-1 border-t border-border pt-3">
            <label className="flex items-center gap-2 font-medium">
              <input
                type="checkbox"
                checked={s.tag_editing}
                disabled={setEditing.isPending}
                onChange={(e) => setEditing.mutate(e.target.checked)}
              />
              Allow editing tags
            </label>
            <p className="text-xs text-muted">
              Album pages get an Edit tags button. Changes are written to your music files by the worker, after you've
              reviewed them; every change is recorded and can be undone, and a file changed elsewhere is never
              overwritten.
            </p>
            {setEditing.isError && <Notice tone="error">{errorMessage(setEditing.error)}</Notice>}
          </div>
        )}
      </div>
    </Card>
  )
}

const COLUMNS: [keyof TrackFile['plex'] & string, string][] = [
  ['track', '#'],
  ['title', 'Title'],
  ['artist', 'Artist'],
  ['albumartist', 'Album artist'],
  ['album', 'Album'],
  ['year', 'Year'],
  ['genre', 'Genre'],
]

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '–'
  return Array.isArray(value) ? value.join(', ') || '–' : String(value)
}

function fileValue(file: TrackFile, field: string): unknown {
  const tags = file.tags as Record<string, unknown>
  return field === 'year' ? (typeof tags.date === 'string' ? tags.date.slice(0, 4) : null) : tags[field]
}

/** An album's songs with the tags in their files, where they differ from Plex highlighted. */
export function AlbumFiles({ serverId, albumKey }: { serverId: number; albumKey: number }) {
  const files = useAlbumFiles(serverId, albumKey, true)
  const summary = useFilesSummary(serverId)
  const [editing, setEditing] = useState(false)
  const [lookup, setLookup] = useState(false)
  if (files.isPending) return null
  if (files.isError) return null // not the owner (403), or nothing to show
  const { tracks, scanned } = files.data
  const different = tracks.filter((t) => t.differences.length > 0).length
  const withFiles = tracks.filter((t) => t.file_id && !t.error)
  const canEdit = Boolean(summary.data?.tag_editing) && withFiles.length > 0
  return (
    <div className="space-y-4">
    {lookup && (
      <MusicBrainzLookup
        serverId={serverId}
        albumKey={albumKey}
        album={String(tracks[0]?.plex.album ?? '')}
        artist={String(tracks[0]?.plex.albumartist ?? '')}
        fileCount={withFiles.length}
        canWrite={Boolean(summary.data?.tag_editing)}
        onClose={() => setLookup(false)}
      />
    )}
    <Card
      title="Tags in the files"
      action={
        scanned ? (
          <span className="flex items-center gap-3">
            <span className="text-xs text-muted">
              {different ? `${different} of ${tracks.length} songs differ from Plex` : 'Same as Plex'}
            </span>
            {withFiles.length > 0 && !editing && (
              <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={() => setLookup(true)}>
                MusicBrainz…
              </Button>
            )}
            {canEdit && !editing && (
              <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={() => setEditing(true)}>
                Edit tags
              </Button>
            )}
          </span>
        ) : undefined
      }
    >
      {editing ? (
        <TagEditor serverId={serverId} tracks={tracks} onClose={() => setEditing(false)} />
      ) : !scanned ? (
        <p className="text-sm text-muted">
          Scan your music folder (
          <Link to="/settings/files" className="text-accent underline">
            Settings → Music files
          </Link>
          ) to see the tags in these songs' files.
        </p>
      ) : (
        <div className="-mx-5 overflow-x-auto px-5">
          <table className="w-full min-w-[40rem] text-sm">
            <thead className="text-left text-xs uppercase tracking-wider text-muted">
              <tr>
                {COLUMNS.map(([key, label]) => (
                  <th key={key} className="px-2 pb-2 font-semibold">
                    {label}
                  </th>
                ))}
                <th className="px-2 pb-2 font-semibold">File</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {tracks.map((t) => (
                <tr key={t.rating_key} className="align-top">
                  {t.file === null || t.error ? (
                    <td colSpan={COLUMNS.length + 1} className="px-2 py-2 text-muted">
                      {show(t.plex.title)}: {t.error ?? 'no file found for this song (is it in the music folder?)'}
                    </td>
                  ) : (
                    <>
                      {COLUMNS.map(([key]) => {
                        const differs = t.differences.includes(key)
                        const value = show(fileValue(t, key))
                        return (
                          <td key={key} className="max-w-48 px-2 py-2">
                            {differs ? (
                              <Tooltip content={`Plex: ${show(t.plex[key])}`}>
                                <span className="rounded bg-warn/15 px-1 text-warn" aria-label={`${value} (Plex: ${show(t.plex[key])})`}>
                                  {value}
                                </span>
                              </Tooltip>
                            ) : (
                              <span className="break-words">{value}</span>
                            )}
                          </td>
                        )
                      })}
                      <td className="px-2 py-2 text-xs text-muted">
                        <span className="block max-w-56 truncate font-mono" title={t.file}>
                          {t.file}
                        </span>
                        {t.format?.toUpperCase()}
                        {t.duration_ms ? ` · ${formatDuration(t.duration_ms)}` : ''}
                        {t.has_cover ? ' · cover' : ''}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-muted">
            Highlighted: different from Plex (hover for Plex's value).
            {!summary.data?.tag_editing && (
              <>
                {' '}
                To edit tags, allow it in{' '}
                <Link to="/settings/files" className="underline">
                  Settings → Music files
                </Link>
                .
              </>
            )}
          </p>
        </div>
      )}
    </Card>
    <TagHistory serverId={serverId} album={albumKey} />
    </div>
  )
}

/** An artist's files: make their tags match Plex (server owners, once scanned). */
export function ArtistFiles({ serverId, artistKey }: { serverId: number; artistKey: number }) {
  const summary = useFilesSummary(serverId)
  const [open, setOpen] = useState(false)
  if (!summary.data?.mapped || !summary.data.files) return null
  return (
    <Card
      title="Tags in the files"
      action={
        !open ? (
          <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={() => setOpen(true)}>
            Take tags from Plex…
          </Button>
        ) : undefined
      }
    >
      {open ? (
        <PlexSync serverId={serverId} scope="artist" scopeKey={artistKey} canWrite={summary.data.tag_editing} />
      ) : (
        <p className="text-sm text-muted">Make this artist's files' tags match Plex, or edit them album by album.</p>
      )}
    </Card>
  )
}
