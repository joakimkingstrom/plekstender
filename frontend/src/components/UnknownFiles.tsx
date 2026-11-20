import { Select } from './Select'
import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import {
  useIdentifyApply,
  useIdentifyPreview,
  useIdentifyStatus,
  useStartIdentify,
  useUnknownFiles,
  type IdentityChoice,
  type UnknownFile,
  type UnknownKind,
} from '../api/files'
import { formatCount } from '../format'
import { fieldLabel, showFieldValue, showOld } from '../tags'
import { Dialog } from './Dialog'
import { Segmented } from './Segmented'
import { Button, Card, Notice, Spinner } from './ui'

const KINDS: [UnknownKind, string][] = [
  ['missing', 'Missing tags'],
  ['unmatched', 'Not in Plex'],
  ['no_mbid', 'No MusicBrainz ID'],
]
const EMPTY: Record<UnknownKind, string> = {
  missing: 'Every file has a title, artist and album.',
  unmatched: 'Plex has every file.',
  no_mbid: 'Every file has a MusicBrainz recording ID.',
}
const MAX_FILES = 5000

const choiceValue = (c: { recording_id: string; track_id?: string | null }) => `${c.recording_id}|${c.track_id ?? ''}`

/** Every match for a file, as options: each recording on each of its releases, or alone. */
function options(file: UnknownFile): { value: string; label: string }[] {
  const out = []
  for (const result of file.results) {
    const score = `${Math.round(result.score * 100)}%`
    for (const rec of result.recordings) {
      const song = `${rec.title} · ${rec.artist}`
      for (const rel of rec.releases) {
        const facts = [rel.date?.slice(0, 4), rel.country, rel.format].filter(Boolean).join(', ')
        out.push({
          value: choiceValue({ recording_id: rec.id, track_id: rel.track_id }),
          label: `${song} · ${rel.title}${facts ? ` (${facts})` : ''} · ${score}`,
        })
      }
      out.push({ value: choiceValue({ recording_id: rec.id }), label: `${song} · just the song · ${score}` })
    }
  }
  return out
}

/**
 * Files Plex shows as unknown (missing tags), files Plex doesn't have, or files without a
 * MusicBrainz ID: identify them by how they sound (AcoustID), pick the matches, preview and
 * write their tags.
 */
export function UnknownFiles({ serverId, canWrite, isAdmin }: { serverId: number; canWrite: boolean; isAdmin: boolean }) {
  const [kind, setKind] = useState<UnknownKind>('missing')
  const files = useUnknownFiles(serverId, kind)
  const status = useIdentifyStatus(serverId)
  const start = useStartIdentify(serverId)
  // Picked matches per file (file id -> "recording|track"); "" leaves the file alone.
  const [picked, setPicked] = useState<Record<number, string>>({})
  const [reviewing, setReviewing] = useState<IdentityChoice[] | null>(null)

  const active = status.data?.active
  const last = status.data?.last
  const { done = 0, total = 0 } = (active?.progress ?? {}) as { done?: number; total?: number }
  const list = files.data?.files ?? []
  const choice = (f: UnknownFile) => picked[f.file_id] ?? (f.suggestion ? choiceValue(f.suggestion) : '')
  const choices: IdentityChoice[] = list.flatMap((f) => {
    const value = f.status === 'identified' ? choice(f) : ''
    if (!value) return []
    const [recording_id, track_id] = value.split('|')
    return [{ file_id: f.file_id, recording_id, track_id: track_id || null }]
  })
  const unidentified = list.filter((f) => f.status === 'new' || f.status === 'error').length

  return (
    <Card title="Unknown files">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Find out what songs are by how they sound (AcoustID and MusicBrainz), then write their tags.
        </p>
        <Segmented value={kind} options={KINDS} onChange={setKind} label="Which files" className="w-full" />
        {files.isError && <Notice tone="error">{errorMessage(files.error)}</Notice>}
        {files.data && files.data.total === 0 && <p className="text-muted">None. {EMPTY[kind]}</p>}

        {files.data && files.data.total > 0 && (
          <>
            {status.data && !status.data.configured ? (
              <Notice>
                Identifying files needs an AcoustID application key
                {isAdmin ? (
                  <>
                    {' '}
                    (
                    <Link to="/settings/services" className="text-accent underline">
                      Settings → Services
                    </Link>
                    )
                  </>
                ) : (
                  ': ask an admin to add one'
                )}
                .
              </Notice>
            ) : active ? (
              <div className="space-y-1">
                <p className="flex justify-between">
                  <span>Identifying by sound…</span>
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
              <div className="flex flex-wrap items-center gap-3">
                <span>
                  {formatCount(files.data.total)} {files.data.total === 1 ? 'file' : 'files'}
                </span>
                {unidentified > 0 || files.data.total > list.length ? (
                  <Button
                    variant="secondary"
                    className="px-3 py-1.5 text-xs"
                    busy={start.isPending}
                    onClick={() => start.mutate({ kind, again: false })}
                  >
                    Identify {files.data.total > MAX_FILES ? `the first ${formatCount(MAX_FILES)}` : 'them'} by sound
                  </Button>
                ) : null}
              </div>
            )}
            {start.isError && <Notice tone="error">{errorMessage(start.error)}</Notice>}
            {last?.status === 'failed' && !active && <Notice tone="error">{last.error}</Notice>}
            {last?.status === 'succeeded' && !active && <LastRun stats={last.stats} />}

            <ul className="divide-y divide-border rounded-lg border border-border">
              {list.map((f) => (
                <FileRow
                  key={f.file_id}
                  file={f}
                  value={choice(f)}
                  onChange={(value) => setPicked((p) => ({ ...p, [f.file_id]: value }))}
                />
              ))}
            </ul>
            {files.data.total > list.length && (
              <p className="text-xs text-muted">The first {list.length} are listed; write these, and the next ones show.</p>
            )}
            {choices.length > 0 &&
              (canWrite ? (
                <div className="flex justify-end">
                  <Button variant="primary" onClick={() => setReviewing(choices)}>
                    Check the changes for {choices.length} {choices.length === 1 ? 'file' : 'files'}
                  </Button>
                </div>
              ) : (
                <Notice>Allow changing files (above) to write the matches into the files.</Notice>
              ))}
          </>
        )}
      </div>
      {reviewing && <Review serverId={serverId} choices={reviewing} onClose={() => setReviewing(null)} />}
    </Card>
  )
}

function LastRun({ stats }: { stats: Record<string, unknown> }) {
  const n = (key: string) => Number(stats[key] ?? 0)
  const parts = [
    `${formatCount(n('identified'))} identified`,
    n('no_match') ? `${formatCount(n('no_match'))} not recognised` : null,
    n('errors') ? `${formatCount(n('errors'))} couldn't be read` : null,
  ].filter(Boolean)
  return <p className="text-xs text-muted">Last run: {parts.join(', ')}.</p>
}

function FileRow({ file, value, onChange }: { file: UnknownFile; value: string; onChange: (value: string) => void }) {
  const name = file.path.split('/').at(-1) ?? file.path
  const tags = [file.tags.title, file.tags.artist, file.tags.album].map((t) => (t ? String(t) : '—')).join(' · ')
  return (
    <li className="space-y-1.5 p-2.5">
      <div className="min-w-0">
        <p className="truncate font-mono text-xs" title={file.path}>
          {name}
        </p>
        <p className="truncate text-xs text-muted">
          {tags}
          {!file.in_plex && ' · not in Plex'}
        </p>
      </div>
      {file.status === 'identified' ? (
        <Select
          size="sm"
          label={`Match for ${name}`}
          value={value}
          className="w-full min-w-0"
          options={[{ value: '', label: 'Leave this file alone' }, ...options(file)]}
          onChange={onChange}
        />
      ) : (
        <p className={`text-xs ${file.status === 'error' ? 'text-danger' : 'text-muted'}`}>
          {file.status === 'new'
            ? 'Not identified yet.'
            : file.status === 'no_match'
              ? "AcoustID doesn't recognise it."
              : file.error}
        </p>
      )}
    </li>
  )
}

function Review({ serverId, choices, onClose }: { serverId: number; choices: IdentityChoice[]; onClose: () => void }) {
  const preview = useIdentifyPreview(serverId, choices)
  const apply = useIdentifyApply(serverId)
  const data = preview.data
  return (
    <Dialog
      title="Write these tags?"
      subtitle={
        data
          ? `${formatCount(data.changes)} ${data.changes === 1 ? 'change' : 'changes'} in ${data.files.length} ${data.files.length === 1 ? 'file' : 'files'}. Undo them from the tag history.`
          : undefined
      }
      onClose={onClose}
    >
      <div className="space-y-4 text-sm">
        {preview.isPending && (
          <div className="flex justify-center py-6">
            <Spinner />
          </div>
        )}
        {preview.isError && <Notice tone="error">{errorMessage(preview.error)}</Notice>}
        {data && data.changes === 0 && <Notice>The files already have these tags.</Notice>}
        {data && data.changes > 0 && (
          <>
            <ul className="max-h-[50dvh] space-y-3 overflow-y-auto">
              {data.files.map((f) => {
                const ids = f.changes.filter((c) => c.field.startsWith('musicbrainz_')).length
                return (
                  <li key={f.file_id}>
                    <p className="truncate font-mono text-xs text-muted" title={f.path}>
                      {f.path}
                    </p>
                    <ul className="mt-1 space-y-0.5 text-xs">
                      {f.changes
                        .filter((c) => !c.field.startsWith('musicbrainz_'))
                        .map((c) => (
                          <li key={c.field} className="flex flex-wrap gap-x-2">
                            <span className="w-28 shrink-0 text-muted">{fieldLabel(c.field)}</span>
                            <span className="text-danger line-through decoration-1">{showOld(c)}</span>
                            <span aria-hidden>→</span>
                            <span className="font-medium text-ok">{showFieldValue(c.field, c.new_value)}</span>
                          </li>
                        ))}
                      {ids > 0 && <li className="text-muted">+ {ids} MusicBrainz {ids === 1 ? 'ID' : 'IDs'}</li>}
                    </ul>
                  </li>
                )
              })}
            </ul>
            {apply.isError && <Notice tone="error">{errorMessage(apply.error)}</Notice>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>
                Back
              </Button>
              <Button variant="primary" busy={apply.isPending} onClick={() => apply.mutate(choices, { onSuccess: onClose })}>
                Write {formatCount(data.changes)} {data.changes === 1 ? 'change' : 'changes'}
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  )
}
