import { useMemo, useState } from 'react'
import { errorMessage } from '../api/client'
import { useEditTags, useTagHistory, useUndoTags, type TagChange, type TagChangeSet, type TrackFile } from '../api/files'
import { formatCount, formatRelative } from '../format'
import {
  ALBUM_FIELDS,
  FIELD_LABELS,
  fieldLabel,
  SONG_FIELDS,
  asText,
  plannedChanges,
  showFieldValue,
  showValue,
  type Draft,
  type EditableFile,
  type PlannedChange,
  type TagField,
} from '../tags'
import { Dialog } from './Dialog'
import { Badge, Button, Card, Notice, Spinner } from './ui'

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
  const changes = plannedChanges(files, draft)

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
      <div className="-mx-5 overflow-x-auto px-5">
        <table className="w-full min-w-[36rem] text-sm">
          <thead className="text-left text-xs uppercase tracking-wider text-muted">
            <tr>
              <th className="w-20 px-1 pb-2 font-semibold">{FIELD_LABELS.tracknumber}</th>
              <th className="px-1 pb-2 font-semibold">{FIELD_LABELS.title}</th>
              <th className="px-1 pb-2 font-semibold">{FIELD_LABELS.artist}</th>
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.file_id}>
                {SONG_FIELDS.map((field) => (
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

/**
 * The history of edits to an album's or an artist's files, newest first. Each edit can be
 * undone as a whole, or change by change (pick them).
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
            className="px-3 py-1.5 text-xs"
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
        <ul className="space-y-2">
          {history.data.map((set) => {
            const status = STATUS[set.status]
            const counts = set.counts ?? {}
            const done = set.total - (counts.pending ?? 0)
            const skipped = (counts.conflict ?? 0) + (counts.failed ?? 0)
            return (
              <li key={set.id} className="rounded-xl border border-border">
                <details>
                  <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                    <span className="font-medium">{set.description}</span>
                    <span className="text-xs text-muted">
                      {set.user ?? 'someone'} · {formatRelative(set.created_at)}
                    </span>
                    <span className="ml-auto flex items-center gap-2">
                      {set.total > 1 && (
                        <span className="text-xs text-muted tabular-nums">
                          {set.status === 'running' || set.status === 'queued'
                            ? `${formatCount(done)} / ${formatCount(set.total)}`
                            : `${formatCount(counts.applied ?? 0)} written${skipped ? `, ${formatCount(skipped)} skipped` : ''}`}
                        </span>
                      )}
                      {(set.status === 'queued' || set.status === 'running') && <Spinner small />}
                      <Badge tone={status.tone}>{status.label}</Badge>
                    </span>
                  </summary>
                  <div className="space-y-2 border-t border-border px-3 py-2">
                    <ul className="space-y-1">
                      {set.changes.map((c) => (
                        <li key={c.id} className="flex items-start gap-2">
                          <input
                            type="checkbox"
                            className="mt-1"
                            aria-label={`Pick ${fieldLabel(c.field)} of ${c.path}`}
                            disabled={!undoable(c)}
                            checked={picked.has(c.id)}
                            onChange={() => toggle(c.id)}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-mono text-xs text-muted" title={c.path}>
                              {c.path.split('/').at(-1)}
                            </span>
                            <span className="flex flex-wrap gap-x-2">
                              <span className="text-muted">{fieldLabel(c.field)}</span>
                              <span>{showFieldValue(c.field, c.old_value)}</span>
                              <span aria-hidden>→</span>
                              <span className="font-medium">{showFieldValue(c.field, c.new_value)}</span>
                              {c.undone && <Badge>undone</Badge>}
                              {c.status === 'conflict' && <Badge tone="accent">skipped: changed elsewhere</Badge>}
                              {c.status === 'failed' && <Badge tone="accent">failed</Badge>}
                            </span>
                            {c.error && c.status !== 'applied' && <span className="block text-xs text-muted">{c.error}</span>}
                          </span>
                        </li>
                      ))}
                    </ul>
                    {set.total > set.changes.length && (
                      <p className="text-xs text-muted">
                        The first {set.changes.length} of {formatCount(set.total)} changes are listed.
                      </p>
                    )}
                    {set.undoable > 0 && (
                      <Button
                        variant="ghost"
                        className="px-2 py-1 text-xs"
                        busy={undo.isPending}
                        onClick={() => undo.mutate({ setId: set.id })}
                      >
                        ↶ Undo this edit{set.undoable > 1 ? ` (${formatCount(set.undoable)} changes)` : ''}
                      </Button>
                    )}
                  </div>
                </details>
              </li>
            )
          })}
        </ul>
      </div>
    </Card>
  )
}
