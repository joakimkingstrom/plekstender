import { useState } from 'react'
import { errorMessage } from '../api/client'
import { usePlexSync, usePlexSyncPreview, type PlexSyncRequest } from '../api/files'
import { formatCount } from '../format'
import { FIELD_LABELS, fieldLabel, showFieldValue, showOld, type TagField } from '../tags'
import { Button, Notice } from './ui'

type SyncField = NonNullable<PlexSyncRequest['fields']>[number]
const FIELDS: SyncField[] = ['title', 'artist', 'album', 'albumartist', 'tracknumber', 'discnumber', 'date', 'genre', 'rating']
// Your ratings only when asked for.
const DEFAULT_FIELDS = FIELDS.filter((f) => f !== 'rating')

/**
 * Writing Plex's metadata to the files (a whole library or an artist): choose the fields,
 * check what would change, then write it as one edit (undo it as a whole from the history).
 */
export function PlexSync({
  serverId,
  scope,
  scopeKey,
  canWrite,
  onWritten,
}: {
  serverId: number
  scope: PlexSyncRequest['scope']
  scopeKey?: number
  canWrite: boolean
  onWritten?: () => void
}) {
  const [fields, setFields] = useState<Set<SyncField>>(new Set(DEFAULT_FIELDS))
  const preview = usePlexSyncPreview(serverId)
  const write = usePlexSync(serverId)
  const request: PlexSyncRequest = { scope, key: scopeKey ?? null, fields: FIELDS.filter((f) => fields.has(f)) }
  const toggle = (field: SyncField) => {
    preview.reset()
    setFields((current) => {
      const next = new Set(current)
      if (next.has(field)) next.delete(field)
      else next.add(field)
      return next
    })
  }
  const data = preview.data

  return (
    <div className="space-y-3 text-sm">
      <fieldset className="flex flex-wrap gap-x-4 gap-y-1.5">
        <legend className="mb-1.5 font-medium">Tags to take from Plex</legend>
        {DEFAULT_FIELDS.map((f) => (
          <label key={f} className="flex items-center gap-1.5">
            <input type="checkbox" checked={fields.has(f)} onChange={() => toggle(f)} />
            {FIELD_LABELS[f as TagField]}
          </label>
        ))}
        <label className="flex basis-full items-start gap-1.5 pt-1">
          <input type="checkbox" className="mt-0.5" checked={fields.has('rating')} onChange={() => toggle('rating')} />
          <span>
            Your ratings
            <span className="block text-xs text-muted">
              The songs you've rated, as POPM in MP3s and RATING (0–100) in FLAC, Ogg and M4A files, which most
              players read.
            </span>
          </span>
        </label>
      </fieldset>
      <Button variant="secondary" disabled={fields.size === 0} busy={preview.isPending} onClick={() => preview.mutate(request)}>
        Check what would change
      </Button>
      {preview.isError && <Notice tone="error">{errorMessage(preview.error)}</Notice>}
      {data &&
        (data.changes === 0 ? (
          <Notice>The files already match Plex{fields.size < FIELDS.length ? ' for these tags' : ''}.</Notice>
        ) : (
          <div className="space-y-3">
            <p>
              <strong>
                {formatCount(data.changes)} {data.changes === 1 ? 'change' : 'changes'}
              </strong>{' '}
              in {formatCount(data.files)} {data.files === 1 ? 'file' : 'files'}:{' '}
              {Object.entries(data.by_field)
                .map(([f, n]) => `${fieldLabel(f)} ${formatCount(n)}`)
                .join(', ')}
              .
            </p>
            <div className="max-h-72 overflow-y-auto rounded-lg border border-border">
              <table className="w-full text-xs">
                <tbody className="divide-y divide-border">
                  {data.samples.map((c, i) => (
                    <tr key={i}>
                      <td className="max-w-56 truncate px-2 py-1 font-mono text-muted" title={c.path}>
                        {c.path.split('/').at(-1)}
                      </td>
                      <td className="px-2 py-1 text-muted">{fieldLabel(c.field)}</td>
                      <td className="px-2 py-1">
                        <span className="text-danger line-through decoration-1">{showOld(c)}</span> →{' '}
                        <span className="text-ok">{showFieldValue(c.field, c.new_value)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {data.changes > data.samples.length && (
              <p className="text-xs text-muted">The first {data.samples.length} are shown.</p>
            )}
            {canWrite ? (
              <Button
                variant="primary"
                busy={write.isPending}
                onClick={() =>
                  write.mutate(request, {
                    onSuccess: () => {
                      preview.reset()
                      onWritten?.()
                    },
                  })
                }
              >
                Write {formatCount(data.changes)} {data.changes === 1 ? 'change' : 'changes'}
              </Button>
            ) : (
              <Notice>Allow changing files (above) to write these.</Notice>
            )}
            {write.isError && <Notice tone="error">{errorMessage(write.error)}</Notice>}
          </div>
        ))}
      {write.isSuccess && !data && (
        <Notice>
          Writing in the background: follow it in the tag history (album and artist pages), where it can be undone as
          a whole.
        </Notice>
      )}
    </div>
  )
}
