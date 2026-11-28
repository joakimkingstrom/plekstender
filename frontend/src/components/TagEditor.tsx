import { Link } from 'react-router'
import { useMemo, useState } from 'react'
import { errorMessage } from '../api/client'
import { pictureUrl, useEditTags, useTagHistory, useUndoTags, type TagChange, type TagChangeSet, type TrackFile } from '../api/files'
import { formatCount, formatRelative } from '../format'
import {
  ALBUM_FIELDS,
  FIELD_LABELS,
  MB_PAGES,
  MORE_ALBUM_FIELDS,
  MORE_SONG_FIELDS,
  fieldLabel,
  SONG_FIELDS,
  asText,
  plannedChanges,
  showFieldValue,
  songName,
  showValue,
  type Draft,
  type EditableFile,
  type PlannedChange,
  type TagField,
} from '../tags'
import { Dialog } from './Dialog'
import { JobStop } from './JobStop'
import { Badge, Button, Card, Notice, Spinner, UndoIcon } from './ui'

const input = 'w-full rounded-md border border-border bg-surface-2 px-2 py-1 text-sm'

/** The album's common value for a field ("" when the songs differ). */
function common(files: EditableFile[], field: TagField): string {
  const values = new Set(files.map((f) => asText(f.tags[field])))
  return values.size === 1 ? [...values][0] : ''
}

/**
 * Editing an album's tags: the album-wide fields for every song at once, and per song the
 * track number, title and artist. Nothing is written until the changes are reviewed.
 */
export function TagEditor({
  serverId,
  tracks,
  onClose,
}: {
  serverId: number
  tracks: TrackFile[]
  onClose: () => void
}) {
  const files: EditableFile[] = useMemo(
    () =>
      tracks
        .filter((t) => t.file_id !== null && t.file_id !== undefined && !t.error)
        .map((t) => ({ file_id: t.file_id!, tags: t.tags as Record<string, unknown>, path: t.file ?? '', title: String(t.plex.title ?? '') })),
    [tracks],
  )
  const [draft, setDraft] = useState<Draft>({ album: {}, songs: {} })
  const [reviewing, setReviewing] = useState(false)
  const [more, setMore] = useState(false)
  const changes = plannedChanges(files, draft)
  const songFields = more ? [...SONG_FIELDS, ...MORE_SONG_FIELDS] : SONG_FIELDS

  const setAlbum = (field: TagField, value: string) => setDraft((d) => ({ ...d, album: { ...d.album, [field]: value } }))
  const setSong = (fileId: number, field: TagField, value: string) =>
    setDraft((d) => ({ ...d, songs: { ...d.songs, [fileId]: { ...d.songs[fileId], [field]: value } } }))

  /** Copy Plex's values into the form (only where they differ from the file). */
  const fromPlex = () => {
    const plexOf = (t: TrackFile, field: TagField): string => {
      const plex = t.plex as Record<string, unknown>
      if (field === 'date') return asText(plex.year)
      if (field === 'tracknumber') return asText(plex.track)
      return asText(plex[field])
    }
    const first = tracks.find((t) => t.file_id)
    const album: Partial<Record<TagField, string>> = {}
    if (first) for (const field of ALBUM_FIELDS) album[field] = plexOf(first, field)
    const songs: Draft['songs'] = {}
    for (const t of tracks) {
      if (!t.file_id) continue
      songs[t.file_id] = Object.fromEntries(SONG_FIELDS.map((f) => [f, plexOf(t, f)]))
    }
    // Keep the file's own track number format ("2/12") when the number is the same.
    for (const f of files) {
      const own = asText(f.tags.tracknumber)
      if (own.split('/')[0] === songs[f.file_id]?.tracknumber) songs[f.file_id].tracknumber = own
    }
    // A full date ("1997-05-21") already is the year Plex has.
    if (album.date && files.every((f) => asText(f.tags.date).startsWith(album.date!))) album.date = undefined
    setDraft({ album, songs })
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {ALBUM_FIELDS.map((field) => (
          <label key={field} className="block text-sm">
            <span className="mb-1 block font-medium">
              {FIELD_LABELS[field]} <span className="font-normal text-muted">(every song)</span>
            </span>
            <input
              value={draft.album[field] ?? common(files, field)}
              placeholder={common(files, field) ? undefined : 'Differs per song'}
              onChange={(e) => setAlbum(field, e.target.value)}
              className={input}
            />
          </label>
        ))}
      </div>
      <button
        type="button"
        aria-expanded={more}
        onClick={() => setMore((m) => !m)}
        className="text-sm font-medium text-accent hover:underline"
      >
        {more ? 'Fewer fields' : 'More fields'}: release details, MusicBrainz IDs, disc, and composer
      </button>
      {more && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {MORE_ALBUM_FIELDS.map((field) => (
            <label key={field} className="block text-sm">
              <span className="mb-1 block font-medium">{FIELD_LABELS[field]}</span>
              <input
                value={draft.album[field] ?? common(files, field)}
                placeholder={common(files, field) ? undefined : files.some((f) => f.tags[field]) ? 'Differs per song' : 'None'}
                onChange={(e) => setAlbum(field, e.target.value)}
                className={`${input} ${field.startsWith('musicbrainz_') ? 'font-mono text-xs' : ''}`}
              />
            </label>
          ))}
        </div>
      )}
      <div className="-mx-5 overflow-x-auto px-5">
        <table className={`w-full text-sm ${more ? 'min-w-[48rem]' : 'min-w-[36rem]'}`}>
          <thead className="text-left text-xs uppercase tracking-wider text-muted">
            <tr>
              <th className="w-20 px-1 pb-2 font-semibold">{FIELD_LABELS.tracknumber}</th>
              <th className="px-1 pb-2 font-semibold">{FIELD_LABELS.title}</th>
              <th className="px-1 pb-2 font-semibold">{FIELD_LABELS.artist}</th>
              {more && <th className="w-16 px-1 pb-2 font-semibold">{FIELD_LABELS.discnumber}</th>}
              {more && <th className="px-1 pb-2 font-semibold">{FIELD_LABELS.composer}</th>}
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.file_id}>
                {songFields.map((field) => (
                  <td key={field} className="px-1 py-1">
                    <input
                      aria-label={`${FIELD_LABELS[field]} of ${f.title}`}
                      value={draft.songs[f.file_id]?.[field] ?? asText(f.tags[field])}
                      onChange={(e) => setSong(f.file_id, field, e.target.value)}
                      className={input}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" disabled={changes.length === 0} onClick={() => setReviewing(true)}>
          Review {changes.length || ''} {changes.length === 1 ? 'change' : 'changes'}
        </Button>
        <Button variant="secondary" onClick={fromPlex}>
          Fill in from Plex
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <p className="text-xs text-muted">Genres: separate with commas. Empty removes the tag.</p>
      </div>
      {reviewing && (
        <ReviewDialog
          serverId={serverId}
          changes={changes}
          onClose={() => setReviewing(false)}
          onWritten={() => {
            setReviewing(false)
            onClose()
          }}
        />
      )}
    </div>
  )
}

function ReviewDialog({
  serverId,
  changes,
  onClose,
  onWritten,
}: {
  serverId: number
  changes: PlannedChange[]
  onClose: () => void
  onWritten: () => void
}) {
  const edit = useEditTags(serverId)
  const [description, setDescription] = useState('')
  const byFile = new Map<string, PlannedChange[]>()
  for (const c of changes) byFile.set(c.path, [...(byFile.get(c.path) ?? []), c])
  return (
    <Dialog
      title="Write these changes to the files?"
      subtitle={`${changes.length} ${changes.length === 1 ? 'change' : 'changes'} in ${byFile.size} ${byFile.size === 1 ? 'file' : 'files'}. Each can be undone later.`}
      onClose={onClose}
    >
      <div className="space-y-4 text-sm">
        <ul className="max-h-[50dvh] space-y-3 overflow-y-auto">
          {[...byFile.entries()].map(([path, fileChanges]) => (
            <li key={path}>
              <p className="truncate font-mono text-xs text-muted" title={path}>
                {path}
              </p>
              <ul className="mt-1 space-y-0.5">
                {fileChanges.map((c) => (
                  <li key={c.field} className="flex flex-wrap gap-x-2">
                    <span className="w-24 shrink-0 text-muted">{FIELD_LABELS[c.field]}</span>
                    <span className="text-danger line-through decoration-1">{showValue(c.old)}</span>
                    <span aria-hidden>→</span>
                    <span className="font-medium text-ok">{showValue(c.value)}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
        <label className="block">
          <span className="mb-1 block font-medium">
            Description <span className="font-normal text-muted">(optional, for the history)</span>
          </span>
          <input value={description} maxLength={200} onChange={(e) => setDescription(e.target.value)} className={input} />
        </label>
        {edit.isError && <Notice tone="error">{errorMessage(edit.error)}</Notice>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Back
          </Button>
          <Button
            variant="primary"
            busy={edit.isPending}
            onClick={() =>
              edit.mutate(
                {
                  description: description.trim() || null,
                  changes: changes.map(({ file_id, field, value }) => ({ file_id, field, value })),
                },
                { onSuccess: onWritten },
              )
            }
          >
            Write {changes.length} {changes.length === 1 ? 'change' : 'changes'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

const STATUS: Record<TagChangeSet['status'], { label: string; tone: 'muted' | 'accent' | 'ok' }> = {
  queued: { label: 'Waiting for the worker', tone: 'muted' },
  running: { label: 'Writing…', tone: 'accent' },
  applied: { label: 'Written', tone: 'ok' },
  partial: { label: 'Partly written', tone: 'accent' },
  failed: { label: 'Not written', tone: 'muted' },
}

const undoable = (c: TagChange) => c.status === 'applied' && !c.undone

/** A value as the history shows it: MusicBrainz IDs shortened, linked to their page. */
function HistoryValue({
  serverId,
  field,
  value,
  old,
}: {
  serverId: number
  field: string
  value: TagChange['new_value']
  old?: boolean
}) {
  if ((field === 'picture' || field === 'image') && typeof value === 'string' && /^[0-9a-f]{40}$/.test(value))
    return <img src={pictureUrl(serverId, value)} alt={old ? 'Before' : 'After'} className="inline-block size-12 rounded object-cover align-middle" />
  const page = MB_PAGES[field]
  const text = showFieldValue(field, value)
  if (page && typeof value === 'string' && value.length > 12) {
    const short = `${value.slice(0, 8)}…`
    return old ? (
      <span title={value} className="font-mono">
        {short}
      </span>
    ) : (
      <a
        href={`https://musicbrainz.org/${page}/${value}`}
        target="_blank"
        rel="noreferrer"
        title={value}
        className="font-mono underline decoration-dotted underline-offset-2 hover:text-accent"
      >
        {short}
      </a>
    )
  }
  return <span className="break-words">{text}</span>
}

function ChangeRow({
  serverId,
  change: c,
  picked,
  onToggle,
}: {
  serverId: number
  change: TagChange
  picked: boolean
  onToggle: () => void
}) {
  const written = c.status === 'applied'
  const name = `${fieldLabel(c.field)} of ${songName(c.path)}`
  return (
    <li className="grid grid-cols-[1.25rem_minmax(6rem,10rem)_1fr] items-baseline gap-x-2 py-0.5">
      {undoable(c) ? (
        <input type="checkbox" aria-label={`Pick ${name}`} checked={picked} onChange={onToggle} className="translate-y-0.5" />
      ) : (
        <span />
      )}
      <span className="text-muted">{fieldLabel(c.field)}</span>
      <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span className={written && !c.undone ? 'text-muted line-through decoration-1' : 'text-muted'}>
          {c.old_value === null ? <span className="italic">none</span> : <HistoryValue serverId={serverId} field={c.field} value={c.old_value} old />}
        </span>
        <span aria-hidden className="text-muted">
          →
        </span>
        <span className={written ? 'font-medium' : 'text-muted'}>
          {c.new_value === null ? <span className="italic">removed</span> : <HistoryValue serverId={serverId} field={c.field} value={c.new_value} />}
        </span>
        {c.undone && <Badge>undone</Badge>}
        {c.status === 'conflict' && (
          <span className="text-xs text-warn" title={c.error ?? undefined}>
            left as it was
          </span>
        )}
        {c.status === 'failed' && <span className="text-xs text-danger">not written: {c.error}</span>}
        {c.status === 'pending' && <span className="text-xs text-muted">waiting</span>}
      </span>
      {c.status === 'conflict' && c.error && (
        <span className="col-start-3 text-xs text-muted">{c.error.replace(/^Left as it was: /, '')}</span>
      )}
    </li>
  )
}

/**
 * The history of edits to an album's or an artist's files (or all files), newest first. Each
 * edit lists its changes file by file; it can be undone as a whole, or change by change.
 */
export function TagHistory({ serverId, album, artist }: { serverId: number; album?: number; artist?: number }) {
  const history = useTagHistory(serverId, album !== undefined ? { album } : { artist })
  const undo = useUndoTags(serverId)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  if (history.isPending || history.isError || history.data.length === 0) return null
  const toggle = (id: number) =>
    setPicked((p) => {
      const next = new Set(p)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <Card
      title="Tag history"
      action={
        picked.size > 0 ? (
          <Button
            variant="secondary"
            size="sm"
            busy={undo.isPending}
            onClick={() => undo.mutate([...picked], { onSuccess: () => setPicked(new Set()) })}
          >
            Undo {picked.size} picked
          </Button>
        ) : undefined
      }
    >
      <div className="space-y-2 text-sm">
        {undo.isError && <Notice tone="error">{errorMessage(undo.error)}</Notice>}
        <p className="text-xs text-muted">
          Edits that wrote something. Ones still waiting or being written, and ones that wrote nothing, are on{' '}
          <Link to="/settings/jobs" className="underline hover:text-text">
            Settings → Jobs
          </Link>
          .
        </p>
        <ul className="space-y-2">
          {history.data.map((set) => {
            const status = STATUS[set.status]
            const counts = set.counts ?? {}
            const done = set.total - (counts.pending ?? 0)
            const left = counts.conflict ?? 0
            const failed = counts.failed ?? 0
            const byFile = new Map<string, TagChange[]>()
            for (const c of set.changes) byFile.set(c.path, [...(byFile.get(c.path) ?? []), c])
            return (
              <li key={set.id} className="rounded-xl border border-border">
                <details>
                  <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                    <span className="font-medium">{set.description}</span>
                    <span className="text-xs text-muted">
                      {set.user ?? 'someone'} · {formatRelative(set.created_at)}
                    </span>
                    <span className="ml-auto flex items-center gap-2">
                      <span className="text-xs text-muted tabular-nums">
                        {set.status === 'running' || set.status === 'queued'
                          ? `${formatCount(done)} / ${formatCount(set.total)}`
                          : [
                              `${formatCount(counts.applied ?? 0)} written`,
                              left ? `${formatCount(left)} left as they were` : null,
                              failed ? `${formatCount(failed)} failed` : null,
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                      </span>
                      {(set.status === 'queued' || set.status === 'running') && <Spinner small />}
                      <Badge tone={status.tone}>{status.label}</Badge>
                    </span>
                  </summary>
                  <div className="space-y-3 border-t border-border px-3 py-3">
                    {left > 0 && (
                      <p className="rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn">
                        {formatCount(left)} {left === 1 ? 'change was' : 'changes were'} left as{' '}
                        {left === 1 ? 'it was' : 'they were'}: the file held something other than what Plekstender
                        expected, so it wasn't overwritten. Scan the music folder and make the edit again to update
                        them.
                      </p>
                    )}
                    {[...byFile.entries()].map(([path, changes]) => (
                      <section key={path}>
                        <h4 className="truncate font-medium" title={path}>
                          {songName(path)}
                        </h4>
                        <ul className="mt-0.5">
                          {changes.map((c) => (
                            <ChangeRow key={c.id} serverId={serverId} change={c} picked={picked.has(c.id)} onToggle={() => toggle(c.id)} />
                          ))}
                        </ul>
                      </section>
                    ))}
                    {set.total > set.changes.length && (
                      <p className="text-xs text-muted">
                        The first {set.changes.length} of {formatCount(set.total)} changes are listed.
                      </p>
                    )}
                    {set.undoable > 0 && (
                      <Button
                        variant="ghost"
                        size="xs"
                        busy={undo.isPending}
                        onClick={() => undo.mutate({ setId: set.id })}
                      >
                        <UndoIcon /> Undo this edit{set.undoable > 1 ? ` (${formatCount(set.undoable)} changes)` : ''}
                      </Button>
                    )}
                  </div>
                </details>
                {set.error && !(set.status === 'queued' || set.status === 'running') && (
                  <p className="border-t border-border px-3 py-2 text-xs text-muted">{set.error}</p>
                )}
                {(set.status === 'queued' || set.status === 'running') && (
                  <div className="border-t border-border px-3 py-2">
                    <JobStop
                      kind="edits"
                      jobId={set.id}
                      requested={set.stop_requested}
                      what="the edit"
                      keeps="Stop keeps what's written; the rest isn't."
                      cancel={
                        <p>
                          The rest isn't written, and what it has written so far is undone (as an undo edit, in the
                          history).
                        </p>
                      }
                    />
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      </div>
    </Card>
  )
}
