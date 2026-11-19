import { useState, type FormEvent } from 'react'
import { errorMessage } from '../api/client'
import { mbCoverUrl, useMbApply, useMbPreview, useMbSearch, type MbApply, type MbPreview, type MbRelease } from '../api/files'
import { formatCount, formatDuration } from '../format'
import { fieldLabel, showValue } from '../tags'
import { Dialog } from './Dialog'
import { Badge, Button, Notice, Spinner } from './ui'

type MbField = MbApply['fields'] extends (infer F)[] | undefined ? F : never

const GROUPS: { label: string; fields: MbField[] }[] = [
  { label: 'Titles and artists', fields: ['title', 'artist', 'album', 'albumartist'] },
  { label: 'Track and disc numbers', fields: ['tracknumber', 'discnumber'] },
  { label: 'Dates and country', fields: ['date', 'originaldate', 'releasecountry'] },
  {
    label: 'MusicBrainz IDs',
    fields: [
      'musicbrainz_trackid',
      'musicbrainz_releasetrackid',
      'musicbrainz_albumid',
      'musicbrainz_artistid',
      'musicbrainz_albumartistid',
      'musicbrainz_releasegroupid',
    ],
  },
]
const input = 'min-w-0 flex-1 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm'

/**
 * Picking a MusicBrainz release for an album, the way Picard does: search, choose the
 * release (cover, year, country, format, label), check which file is which track, then write
 * the release's tags as one edit (undo it from the history).
 */
export function MusicBrainzLookup({
  serverId,
  albumKey,
  album,
  artist,
  fileCount,
  canWrite,
  onClose,
}: {
  serverId: number
  albumKey: number
  album: string
  artist: string
  fileCount: number
  canWrite: boolean
  onClose: () => void
}) {
  const [release, setRelease] = useState<MbRelease | null>(null)
  const [search, setSearch] = useState({ album, artist })
  return (
    <Dialog
      title="Find the release on MusicBrainz"
      subtitle={release ? 'Check which file is which track, and what would change.' : `${album} · ${artist}`}
      onClose={onClose}
    >
      {release ? (
        <ReleaseMatch
          serverId={serverId}
          albumKey={albumKey}
          release={release}
          canWrite={canWrite}
          onBack={() => setRelease(null)}
          onWritten={onClose}
        />
      ) : (
        <ReleaseSearch serverId={serverId} search={search} onSearch={setSearch} fileCount={fileCount} onPick={setRelease} />
      )}
    </Dialog>
  )
}

function Cover({ serverId, id, className }: { serverId: number; id: string; className: string }) {
  const [failed, setFailed] = useState(false)
  if (failed) return <span aria-hidden className={`${className} shrink-0 rounded-md bg-surface-2`} />
  return (
    <img
      src={mbCoverUrl(serverId, id)}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className={`${className} shrink-0 rounded-md bg-surface-2 object-cover`}
    />
  )
}

function releaseFacts(r: MbRelease): string {
  const label = [r.label, r.catalog_number].filter(Boolean).join(' ')
  return [r.date, r.country, r.formats, `${r.track_count} tracks`, label, r.type, r.status !== 'Official' ? r.status : null]
    .filter(Boolean)
    .join(' · ')
}

function ReleaseSearch({
  serverId,
  search,
  onSearch,
  fileCount,
  onPick,
}: {
  serverId: number
  search: { album: string; artist: string }
  onSearch: (search: { album: string; artist: string }) => void
  fileCount: number
  onPick: (release: MbRelease) => void
}) {
  const [form, setForm] = useState(search)
  const results = useMbSearch(serverId, search)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    onSearch({ ...form })
  }
  return (
    <div className="space-y-3 text-sm">
      <form onSubmit={submit} className="space-y-2">
        <div className="flex flex-wrap gap-2">
          <input
            value={form.album}
            onChange={(e) => setForm({ ...form, album: e.target.value })}
            aria-label="Album, or a MusicBrainz release link"
            placeholder="Album, or a MusicBrainz release link"
            className={input}
          />
          <input
            value={form.artist}
            onChange={(e) => setForm({ ...form, artist: e.target.value })}
            aria-label="Artist"
            placeholder="Artist"
            className={input}
          />
          <Button type="submit" variant="secondary" busy={results.isFetching}>
            Search
          </Button>
        </div>
      </form>
      {results.isError && <Notice tone="error">{errorMessage(results.error)}</Notice>}
      {results.isFetching && !results.data && (
        <div className="flex justify-center py-6">
          <Spinner />
        </div>
      )}
      {results.data?.length === 0 && <Notice>Nothing found. Try fewer words, or paste the release's link.</Notice>}
      {results.data && results.data.length > 0 && (
        <ul className="space-y-1" aria-label="Releases">
          {results.data.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onPick(r)}
                className="flex w-full items-center gap-3 rounded-lg p-2 text-left hover:bg-surface-2"
              >
                <Cover serverId={serverId} id={r.id} className="size-14" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">
                    {r.title}
                    {r.disambiguation && <span className="font-normal text-muted"> ({r.disambiguation})</span>}
                  </span>
                  <span className="block truncate text-xs text-muted">{r.artist}</span>
                  <span className="block text-xs text-muted">{releaseFacts(r)}</span>
                </span>
                {r.track_count === fileCount && <Badge tone="ok">{fileCount} tracks, like your files</Badge>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted">
        Results from MusicBrainz, the open music encyclopedia. Covers from the Cover Art Archive.
      </p>
    </div>
  )
}

function ReleaseMatch({
  serverId,
  albumKey,
  release,
  canWrite,
  onBack,
  onWritten,
}: {
  serverId: number
  albumKey: number
  release: MbRelease
  canWrite: boolean
  onBack: () => void
  onWritten: () => void
}) {
  const [mapping, setMapping] = useState<Record<string, string | null> | null>(null)
  const [groups, setGroups] = useState(() => new Set(GROUPS.map((g) => g.label)))
  const fields = GROUPS.filter((g) => groups.has(g.label)).flatMap((g) => g.fields)
  const body: MbApply | null = fields.length ? { release_id: release.id, mapping, fields } : null
  const preview = useMbPreview(serverId, albumKey, body)
  const apply = useMbApply(serverId, albumKey)
  const data = preview.data

  const toggle = (label: string) =>
    setGroups((current) => {
      const next = new Set(current)
      if (next.has(label)) next.delete(label)
      else next.add(label)
      return next
    })

  const choose = (fileId: number, trackId: string | null) => {
    if (!data) return
    const next: Record<string, string | null> = Object.fromEntries(data.files.map((f) => [String(f.file_id), f.track_id ?? null]))
    // A track goes to one file: whichever file had it is left alone now.
    if (trackId) for (const key of Object.keys(next)) if (next[key] === trackId) next[key] = null
    next[String(fileId)] = trackId
    setMapping(next)
  }

  const files = data?.files.filter((f) => f.changes.length > 0).length ?? 0
  return (
    <div className="space-y-4 text-sm">
      <div className="flex items-center gap-3">
        <Cover serverId={serverId} id={release.id} className="size-16" />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{release.title}</p>
          <p className="truncate text-xs text-muted">{release.artist}</p>
          <p className="text-xs text-muted">{releaseFacts(release)}</p>
        </div>
        <Button variant="ghost" className="px-3 py-1.5 text-xs" onClick={onBack}>
          ← Other releases
        </Button>
      </div>

      <fieldset className="flex flex-wrap gap-x-4 gap-y-1.5">
        <legend className="mb-1.5 font-medium">Tags to take from this release</legend>
        {GROUPS.map((g) => (
          <label key={g.label} className="flex items-center gap-1.5">
            <input type="checkbox" checked={groups.has(g.label)} onChange={() => toggle(g.label)} />
            {g.label}
          </label>
        ))}
      </fieldset>

      {!body && <Notice>Pick at least one kind of tag.</Notice>}
      {preview.isError && <Notice tone="error">{errorMessage(preview.error)}</Notice>}
      {body && preview.isPending && (
        <div className="flex justify-center py-6">
          <Spinner />
        </div>
      )}
      {body && data && <FileMatches data={data} busy={preview.isFetching} onChoose={choose} />}

      {body && data && (
        <div className="space-y-2 border-t border-border pt-3">
          {data.changes === 0 ? (
            <Notice>The files already match this release{groups.size < GROUPS.length ? ' for these tags' : ''}.</Notice>
          ) : canWrite ? (
            <div className="flex flex-wrap items-center justify-end gap-3">
              <span className="text-xs text-muted">
                {formatCount(data.changes)} {data.changes === 1 ? 'change' : 'changes'} in {files} {files === 1 ? 'file' : 'files'}; undo
                them from the history.
              </span>
              <Button
                variant="primary"
                busy={apply.isPending}
                disabled={preview.isFetching}
                onClick={() => apply.mutate(body, { onSuccess: onWritten })}
              >
                Write {formatCount(data.changes)} {data.changes === 1 ? 'change' : 'changes'}
              </Button>
            </div>
          ) : (
            <Notice>Turn on "Allow editing tags" (Settings → Music files) to write these.</Notice>
          )}
          {apply.isError && <Notice tone="error">{errorMessage(apply.error)}</Notice>}
        </div>
      )}
    </div>
  )
}

function FileMatches({
  data,
  busy,
  onChoose,
}: {
  data: MbPreview
  busy: boolean
  onChoose: (fileId: number, trackId: string | null) => void
}) {
  const discs = new Set(data.tracks.map((t) => t.disc)).size
  const tracks = new Map(data.tracks.map((t) => [t.id, t]))
  const used = new Set(data.files.map((f) => f.track_id))
  const unused = data.tracks.filter((t) => !used.has(t.id)).length
  const number = (disc: number | null | undefined, n: number | null | undefined) =>
    n ? `${discs > 1 && disc ? `${disc}-` : ''}${n}. ` : ''
  return (
    <div className={busy ? 'opacity-60 transition-opacity' : undefined}>
      <ul className="divide-y divide-border rounded-lg border border-border">
        {data.files.map((f) => {
          const name = f.title ?? f.path.split('/').at(-1) ?? f.path
          const track = f.track_id ? tracks.get(f.track_id) : undefined
          const diff = track?.length_ms && f.duration_ms ? Math.round((f.duration_ms - track.length_ms) / 1000) : 0
          const ids = f.changes.filter((c) => c.field.startsWith('musicbrainz_')).length
          return (
            <li key={f.file_id} className="space-y-1.5 p-2.5">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <span className="min-w-40 flex-1 truncate" title={f.path}>
                  <span className="text-muted tabular-nums">{number(f.disc, f.track)}</span>
                  {name} <span className="text-xs text-muted tabular-nums">{formatDuration(f.duration_ms)}</span>
                </span>
                <span aria-hidden className="hidden text-muted sm:inline">
                  →
                </span>
                <select
                  value={f.track_id ?? ''}
                  onChange={(e) => onChoose(f.file_id, e.target.value || null)}
                  aria-label={`Release track for ${name}`}
                  className="w-full min-w-0 rounded-md border border-border bg-surface-2 px-2 py-1 text-sm sm:w-72"
                >
                  <option value="">Leave this file alone</option>
                  {data.tracks.map((t) => (
                    <option key={t.id} value={t.id}>
                      {number(t.disc, t.position)}
                      {t.title} ({formatDuration(t.length_ms)})
                    </option>
                  ))}
                </select>
              </div>
              {Math.abs(diff) > 10 && (
                <p className="text-xs text-warn">
                  The file is {Math.abs(diff)} s {diff > 0 ? 'longer' : 'shorter'} than this track: is it the right one?
                </p>
              )}
              {f.changes.length > 0 ? (
                <ul className="space-y-0.5 text-xs">
                  {f.changes
                    .filter((c) => !c.field.startsWith('musicbrainz_'))
                    .map((c) => (
                      <li key={c.field} className="flex flex-wrap gap-x-2">
                        <span className="w-28 shrink-0 text-muted">{fieldLabel(c.field)}</span>
                        <span className="text-danger line-through decoration-1">{showValue(c.old_value)}</span>
                        <span aria-hidden>→</span>
                        <span className="font-medium text-ok">{showValue(c.new_value)}</span>
                      </li>
                    ))}
                  {ids > 0 && <li className="text-muted">+ {ids} MusicBrainz {ids === 1 ? 'ID' : 'IDs'}</li>}
                </ul>
              ) : (
                f.track_id && <p className="text-xs text-muted">Already matches.</p>
              )}
            </li>
          )
        })}
      </ul>
      {unused > 0 && (
        <p className="mt-1.5 text-xs text-muted">
          {unused} of the release's {data.tracks.length} tracks {unused === 1 ? 'has' : 'have'} no file here.
        </p>
      )}
    </div>
  )
}
