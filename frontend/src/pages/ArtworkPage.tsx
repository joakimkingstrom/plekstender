import { useDeferredValue, useState, type ChangeEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import {
  useApplyArtwork,
  useApplyPack,
  useArtworkArtists,
  useArtworkJobs,
  useCandidates,
  useFolderSettings,
  useFonts,
  usePreview,
  useSetArtworkStatus,
  useSetFolderSettings,
  useStartExport,
  useStartFanartJob,
  useUploadPack,
  useUploadImage,
  type ArtworkStatus,
  type CandidateSource,
  type SourceSpec,
  type Transform,
} from '../api/artwork'
import { errorMessage, type Schemas } from '../api/client'
import { useMe } from '../api/hooks'
import { Artwork } from '../components/library'
import { Button, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { useLibraryServer } from '../components/useLibraryServer'
import { formatRelative } from '../format'

type Artist = Schemas['ArtworkArtistOut']
type Tab = CandidateSource | 'upload' | 'text'

const STATUSES: [ArtworkStatus, string][] = [
  ['none', 'To do'],
  ['custom', 'Custom'],
  ['done', 'Done'],
]
const STATUS_DOT: Record<ArtworkStatus, string> = {
  none: 'bg-border',
  custom: 'bg-warn',
  done: 'bg-ok',
}
const TABS: [Tab, string][] = [
  ['fanart', 'fanart.tv'],
  ['plex', 'Plex'],
  ['search', 'Search'],
  ['upload', 'Upload'],
  ['text', 'Text'],
]
const DEFAULT_TRANSFORM: Transform = {
  fit: true,
  zoom: 1,
  contrast: 1,
  invert: false,
  greyscale: false,
  white: false,
  tint: null,
  background: '#000000',
}

export function ArtworkPage() {
  const { active, blocker } = useLibraryServer()
  const me = useMe()
  return (
    <>
      <PageTitle subtitle="Replace artist photos with logos. Changes are saved to Plex for everyone using the server.">
        Artwork
      </PageTitle>
      {blocker ??
        (!active!.is_owner ? (
          <Notice>
            Only the owner of {active!.name} can change artist images.
          </Notice>
        ) : (
          <ArtworkEditor serverId={active!.id} isAdmin={Boolean(me.data?.is_admin)} />
        ))}
    </>
  )
}

function ArtworkEditor({ serverId, isAdmin }: { serverId: number; isAdmin: boolean }) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<ArtworkStatus | null>(null)
  const [selected, setSelected] = useState<Artist | null>(null)
  const q = useDeferredValue(query.trim())
  const artists = useArtworkArtists(serverId, q, status)
  const items = artists.data?.pages.flatMap((p) => p.items) ?? []
  const counts = artists.data?.pages[0]?.counts ?? {}
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  // Keep the selected artist in step with the list (status / thumb after applying).
  const current = selected ? (items.find((a) => a.rating_key === selected.rating_key) ?? selected) : null

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[18rem_1fr]">
      <div className={`space-y-3 ${current ? 'hidden lg:block' : ''}`}>
        <BulkFanart serverId={serverId} todo={counts.none ?? 0} />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search artists"
          aria-label="Search artists"
          className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
        />
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Status">
          {([[null, 'All', total], ...STATUSES.map(([k, l]) => [k, l, counts[k] ?? 0])] as [ArtworkStatus | null, string, number][]).map(
            ([key, label, n]) => (
              <button
                key={label}
                type="button"
                aria-pressed={status === key}
                onClick={() => setStatus(key)}
                className={`rounded-full px-3 py-1 text-xs font-medium ${
                  status === key ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted hover:text-text'
                }`}
              >
                {label} <span className="tabular-nums opacity-70">{n}</span>
              </button>
            ),
          )}
        </div>
        {artists.isPending ? (
          <Spinner small />
        ) : artists.isError ? (
          <Notice tone="error">{errorMessage(artists.error)}</Notice>
        ) : (
          <ul className="max-h-[70vh] divide-y divide-border overflow-y-auto rounded-2xl border border-border bg-surface">
            {items.map((a) => (
              <li key={a.rating_key}>
                <button
                  type="button"
                  onClick={() => setSelected(a)}
                  className={`flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-surface-2 ${
                    current?.rating_key === a.rating_key ? 'bg-surface-2' : ''
                  }`}
                >
                  <Artwork serverId={serverId} path={a.thumb} title={a.title} size={96} round className="size-9 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{a.title}</span>
                  <span className={`size-2 shrink-0 rounded-full ${STATUS_DOT[a.status]}`} title={STATUSES.find(([k]) => k === a.status)?.[1]} />
                </button>
              </li>
            ))}
            {artists.hasNextPage && (
              <li className="p-2 text-center">
                <Button variant="ghost" busy={artists.isFetchingNextPage} onClick={() => void artists.fetchNextPage()}>
                  More
                </Button>
              </li>
            )}
            {items.length === 0 && <li className="p-3 text-sm text-muted">No artists match.</li>}
          </ul>
        )}
        <FolderSettings serverId={serverId} />
        <LogoPacks serverId={serverId} />
      </div>
      {current ? (
        <ArtistEditor key={current.rating_key} serverId={serverId} artist={current} isAdmin={isAdmin} onBack={() => setSelected(null)} />
      ) : (
        <Card>
          <p className="text-sm text-muted">Pick an artist to start.</p>
        </Card>
      )}
    </div>
  )
}

function ArtistEditor({
  serverId,
  artist,
  isAdmin,
  onBack,
}: {
  serverId: number
  artist: Artist
  isAdmin: boolean
  onBack: () => void
}) {
  const [tab, setTab] = useState<Tab>('fanart')
  const [source, setSource] = useState<SourceSpec | null>(null)
  const [transform, setTransform] = useState<Transform>(DEFAULT_TRANSFORM)
  const [notice, setNotice] = useState<string | null>(null)
  const preview = usePreview(serverId, artist.rating_key, source, transform)
  const apply = useApplyArtwork(serverId)
  const setStatus = useSetArtworkStatus(serverId)
  const patch = (p: Partial<Transform>) => setTransform((t) => ({ ...t, ...p }))

  const choose = (spec: SourceSpec) => {
    setNotice(null)
    setSource(spec)
  }

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onBack} className="text-sm text-muted hover:text-text lg:hidden">
          ← Artists
        </button>
        <h2 className="min-w-0 flex-1 truncate text-2xl font-bold">
          <Link to={`/library/artists/${artist.rating_key}`} className="hover:underline">
            {artist.title}
          </Link>
        </h2>
        <div className="inline-flex rounded-lg bg-surface-2 p-0.5" role="group" aria-label="Status">
          {STATUSES.map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={artist.status === key}
              onClick={() => setStatus.mutate({ key: artist.rating_key, status: key })}
              className={`rounded-md px-3 py-1 text-sm ${artist.status === key ? 'bg-surface font-medium shadow-sm' : 'text-muted hover:text-text'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_20rem]">
        <Card>
          <div className="mb-4 flex flex-wrap gap-1 border-b border-border" role="tablist">
            {TABS.map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
                className={`border-b-2 px-3 pb-2 text-sm font-medium ${tab === key ? 'border-accent text-text' : 'border-transparent text-muted hover:text-text'}`}
              >
                {label}
              </button>
            ))}
          </div>
          {tab === 'upload' ? (
            <UploadTab serverId={serverId} onUploaded={(id) => choose({ kind: 'upload', upload_id: id })} />
          ) : tab === 'text' ? (
            <TextTab artist={artist} onChange={(text) => choose({ kind: 'text', text })} />
          ) : (
            <CandidateGrid
              serverId={serverId}
              artist={artist}
              source={tab}
              isAdmin={isAdmin}
              selected={source?.kind === 'candidate' ? (source.token ?? null) : null}
              onChoose={(token) => choose({ kind: 'candidate', token })}
            />
          )}
        </Card>

        <div className="space-y-4">
          <Card title="Preview">
            <div className="grid grid-cols-2 gap-3">
              <Figure label="Now">
                <Artwork serverId={serverId} path={artist.thumb} title={artist.title} size={400} className="w-full" />
              </Figure>
              <Figure label="New">
                <div className="relative aspect-square overflow-hidden rounded-lg bg-surface-2">
                  {preview.url && <img src={preview.url} alt={`New image for ${artist.title}`} className="size-full object-cover" />}
                  {!source && (
                    <span className="absolute inset-0 flex items-center justify-center p-2 text-center text-xs text-muted">
                      Choose an image
                    </span>
                  )}
                  {preview.loading && (
                    <span className="absolute inset-0 flex items-center justify-center bg-bg/40">
                      <Spinner small />
                    </span>
                  )}
                </div>
              </Figure>
            </div>
            {preview.error && <div className="mt-3"><Notice tone="error">{preview.error}</Notice></div>}
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                variant="primary"
                disabled={!source || Boolean(preview.error)}
                busy={apply.isPending}
                onClick={() =>
                  source &&
                  apply.mutate(
                    { key: artist.rating_key, source, transform },
                    {
                      onSuccess: (result) =>
                        setNotice(
                          result.folder
                            ? `Saved to Plex and to ${result.folder}.`
                            : result.folder_error
                              ? `Saved to Plex, but not to the artist folder: ${result.folder_error}`
                              : 'Saved to Plex.',
                        ),
                    },
                  )
                }
              >
                Save to Plex
              </Button>
              <Button variant="ghost" onClick={() => choose({ kind: 'current' })} title="Adjust the image Plex has now">
                Edit current
              </Button>
            </div>
            {apply.isError && <div className="mt-3"><Notice tone="error">{errorMessage(apply.error)}</Notice></div>}
            {notice && <p className="mt-3 text-sm text-ok">{notice}</p>}
            {artist.applied_at && (
              <p className="mt-2 text-xs text-muted">
                Last changed {formatRelative(artist.applied_at)}
                {artist.source && ` (${artist.source})`}.
              </p>
            )}
          </Card>
          <Adjustments transform={transform} onChange={patch} onReset={() => setTransform(DEFAULT_TRANSFORM)} />
        </div>
      </div>
    </div>
  )
}

function Figure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <figure className="space-y-1">
      {children}
      <figcaption className="text-center text-xs text-muted">{label}</figcaption>
    </figure>
  )
}

function CandidateGrid({
  serverId,
  artist,
  source,
  isAdmin,
  selected,
  onChoose,
}: {
  serverId: number
  artist: Artist
  source: CandidateSource
  isAdmin: boolean
  selected: string | null
  onChoose: (token: string) => void
}) {
  const found = useCandidates(serverId, artist.rating_key, source, true)
  if (found.isPending) return <Spinner small />
  if (found.isError) return <Notice tone="error">{errorMessage(found.error)}</Notice>
  const { candidates, note } = found.data
  return (
    <div className="space-y-3">
      {note && (
        <Notice>
          {note}{' '}
          {isAdmin && source === 'fanart' && note.includes('API key') && (
            <Link to="/settings/library" className="underline">
              Add it on Settings → Library.
            </Link>
          )}
        </Notice>
      )}
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 2xl:grid-cols-4">
        {candidates.map((c) => (
          <li key={c.token}>
            <button
              type="button"
              onClick={() => onChoose(c.token)}
              className={`block w-full rounded-lg border-2 p-1 text-left ${
                selected === c.token ? 'border-accent' : 'border-transparent hover:border-border'
              }`}
            >
              {/* Logos are often transparent: show them on a checkerboard-free dark plate. */}
              <div className="flex aspect-[3/2] items-center justify-center rounded bg-[#17181c]">
                <img src={c.thumb} alt="" loading="lazy" className="max-h-full max-w-full object-contain" />
              </div>
              <span className="mt-1 block truncate text-xs text-muted">
                {c.label}
                {c.width && c.height ? ` · ${c.width}×${c.height}` : ''}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function UploadTab({ serverId, onUploaded }: { serverId: number; onUploaded: (id: string) => void }) {
  const upload = useUploadImage(serverId)
  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) upload.mutate(file, { onSuccess: (data) => onUploaded(data.upload_id) })
  }
  return (
    <div className="space-y-3">
      <label className="block">
        <span className="mb-1 block text-sm font-medium">Image file</span>
        <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={onFile} className="text-sm" />
      </label>
      <p className="text-xs text-muted">PNG with transparency works best for logos. Up to 15 MB.</p>
      {upload.isPending && <Spinner small />}
      {upload.isError && <Notice tone="error">{errorMessage(upload.error)}</Notice>}
      {upload.isSuccess && <p className="text-sm text-ok">Uploaded. Adjust it on the right.</p>}
    </div>
  )
}

function TextTab({ artist, onChange }: { artist: Artist; onChange: (text: Schemas['TextLogo']) => void }) {
  const fonts = useFonts()
  const [spec, setSpec] = useState<Schemas['TextLogo']>({
    text: artist.title,
    font: 'Anton',
    rows: 1,
    color: '#ffffff',
    case: 'none',
  })
  const update = (p: Partial<Schemas['TextLogo']>) => {
    const next = { ...spec, ...p }
    setSpec(next)
    if (next.text.trim()) onChange(next)
  }
  const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm'
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="sm:col-span-2">
        <span className="mb-1 block text-sm font-medium">Text</span>
        <input value={spec.text} maxLength={120} onChange={(e) => update({ text: e.target.value })} className={field} />
      </label>
      <label>
        <span className="mb-1 block text-sm font-medium">Font</span>
        <select value={spec.font} onChange={(e) => update({ font: e.target.value })} className={field}>
          {fonts.data?.map((g) => (
            <optgroup key={g.name} label={g.name}>
              {g.fonts.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      <label>
        <span className="mb-1 block text-sm font-medium">Lines</span>
        <select value={spec.rows ?? 1} onChange={(e) => update({ rows: Number(e.target.value) })} className={field}>
          {[1, 2, 3, 4].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span className="mb-1 block text-sm font-medium">Case</span>
        <select value={spec.case ?? 'none'} onChange={(e) => update({ case: e.target.value as Schemas['TextLogo']['case'] })} className={field}>
          <option value="none">As written</option>
          <option value="upper">UPPER CASE</option>
          <option value="lower">lower case</option>
        </select>
      </label>
      <label>
        <span className="mb-1 block text-sm font-medium">Colour</span>
        <input type="color" value={spec.color ?? '#ffffff'} onChange={(e) => update({ color: e.target.value })} className="h-9 w-16 rounded border border-border bg-surface-2" />
      </label>
      <div className="sm:col-span-2">
        <button type="button" className="text-sm text-accent" onClick={() => update({})}>
          Preview this text
        </button>
        <p className="mt-1 text-xs text-muted">Fonts are downloaded from Google Fonts the first time they're used.</p>
      </div>
    </div>
  )
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  disabled,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  disabled?: boolean
  onChange: (value: number) => void
}) {
  return (
    <label className={`block ${disabled ? 'opacity-50' : ''}`}>
      <span className="flex justify-between text-sm">
        <span>{label}</span>
        <span className="text-muted tabular-nums">{value.toFixed(1)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[var(--color-accent)]"
      />
    </label>
  )
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="accent-[var(--color-accent)]" />
      {label}
    </label>
  )
}

function Adjustments({
  transform,
  onChange,
  onReset,
}: {
  transform: Transform
  onChange: (p: Partial<Transform>) => void
  onReset: () => void
}) {
  return (
    <Card
      title="Adjust"
      action={
        <button type="button" className="text-xs text-muted hover:text-text" onClick={onReset}>
          Reset
        </button>
      }
    >
      <div className="space-y-3">
        <Toggle label="Fit with a margin (looks right on phones)" checked={transform.fit ?? true} onChange={(fit) => onChange({ fit })} />
        <Slider label="Zoom" value={transform.zoom ?? 1} min={0.25} max={4} step={0.05} disabled={transform.fit ?? true} onChange={(zoom) => onChange({ zoom })} />
        <Slider label="Contrast" value={transform.contrast ?? 1} min={0} max={3} step={0.1} onChange={(contrast) => onChange({ contrast })} />
        <div className="grid grid-cols-2 gap-2">
          <Toggle label="Invert" checked={transform.invert ?? false} onChange={(invert) => onChange({ invert })} />
          <Toggle label="Greyscale" checked={transform.greyscale ?? false} onChange={(greyscale) => onChange({ greyscale })} />
          <Toggle label="All white" checked={transform.white ?? false} onChange={(white) => onChange({ white })} />
          <Toggle label="Tint" checked={Boolean(transform.tint)} onChange={(on) => onChange({ tint: on ? '#d9821a' : null })} />
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          {transform.tint && (
            <label className="flex items-center gap-2">
              Tint colour
              <input type="color" value={transform.tint} onChange={(e) => onChange({ tint: e.target.value })} className="h-8 w-12 rounded border border-border" />
            </label>
          )}
          <label className="flex items-center gap-2">
            Background
            <input type="color" value={transform.background ?? '#000000'} onChange={(e) => onChange({ background: e.target.value })} className="h-8 w-12 rounded border border-border" />
          </label>
        </div>
      </div>
    </Card>
  )
}

function BulkFanart({ serverId, todo }: { serverId: number; todo: number }) {
  const jobs = useArtworkJobs(serverId)
  const start = useStartFanartJob(serverId)
  const [confirming, setConfirming] = useState(false)
  const latest = jobs.data?.find((j) => j.kind === 'fanart')
  const running = latest && (latest.status === 'queued' || latest.status === 'running')
  const { done = 0, total = 0 } = (latest?.progress ?? {}) as { done?: number; total?: number }
  const stats = (latest?.stats ?? {}) as Record<string, number>

  return (
    <div className="rounded-2xl border border-border bg-surface p-3 text-sm">
      {running ? (
        <div className="space-y-2" aria-live="polite">
          <p>
            Applying fanart.tv logos… <span className="text-muted tabular-nums">{done} / {total || '?'}</span>
          </p>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${total ? (done / total) * 100 : 5}%` }} />
          </div>
        </div>
      ) : confirming ? (
        <div className="space-y-2">
          <p>
            Give all {todo} "To do" artists their most-liked fanart.tv logo and save it to Plex? Artists without one
            are left alone.
          </p>
          <div className="flex gap-2">
            <Button
              variant="primary"
              busy={start.isPending}
              onClick={() => start.mutate({ status: 'none' }, { onSettled: () => setConfirming(false) })}
            >
              Apply to {todo}
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-1">
          <Button variant="secondary" disabled={todo === 0} onClick={() => setConfirming(true)} className="w-full">
            Apply fanart.tv logos to all "To do"
          </Button>
          {latest?.status === 'succeeded' && (
            <p className="text-xs text-muted">
              Last run: {stats.applied ?? 0} applied
              {stats.no_logo ? `, ${stats.no_logo} without a logo` : ''}
              {stats.no_mbid ? `, ${stats.no_mbid} not on MusicBrainz` : ''}
              {stats.failed ? `, ${stats.failed} failed` : ''}.
            </p>
          )}
          {latest?.status === 'failed' && <p className="text-xs text-danger">Last run failed: {latest.error}</p>}
        </div>
      )}
      {start.isError && <div className="mt-2"><Notice tone="error">{errorMessage(start.error)}</Notice></div>}
    </div>
  )
}

function FolderSettings({ serverId }: { serverId: number }) {
  const current = useFolderSettings(serverId)
  const save = useSetFolderSettings(serverId)
  const [form, setForm] = useState<{ plex: string; local: string } | null>(null)
  if (current.isPending || current.isError) return null
  const data = current.data
  const plex = form?.plex ?? data.plex_prefix ?? data.plex_root ?? ''
  const local = form?.local ?? data.local_prefix ?? ''
  const field = 'w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 font-mono text-xs'
  return (
    <details className="rounded-2xl border border-border bg-surface p-3 text-sm">
      <summary className="cursor-pointer font-medium">
        Save to artist folders {data.enabled ? <span className="text-ok">· on</span> : <span className="text-muted">· off</span>}
      </summary>
      <div className="mt-3 space-y-3">
        <p className="text-xs text-muted">
          Also writes <code>artist.jpg</code> into each artist's music folder, for other players. Mount your music
          folder into Plekstender's containers read-write (see <code>compose.override.example.yaml</code>), then map
          Plex's path to it.
        </p>
        <label className="block">
          <span className="mb-1 block text-xs font-medium">Music folder as Plex sees it</span>
          <input value={plex} onChange={(e) => setForm({ plex: e.target.value, local })} className={field} placeholder="/music" />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium">Same folder in Plekstender's container</span>
          <input value={local} onChange={(e) => setForm({ plex, local: e.target.value })} className={field} placeholder="/music" />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            busy={save.isPending}
            onClick={() => save.mutate({ enabled: true, plex_prefix: plex, local_prefix: local }, { onSuccess: () => setForm(null) })}
          >
            {data.enabled ? 'Update' : 'Turn on'}
          </Button>
          {data.enabled && (
            <Button variant="ghost" busy={save.isPending} onClick={() => save.mutate({ enabled: false, plex_prefix: plex, local_prefix: local })}>
              Turn off
            </Button>
          )}
        </div>
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
        {data.enabled && data.example && (
          <p className={`text-xs ${data.example.problem ? 'text-danger' : 'text-muted'}`}>
            Check with {data.example.artist}:{' '}
            {data.example.problem ?? `${data.example.local_folder} ✓`}
          </p>
        )}
      </div>
    </details>
  )
}

function JobProgress({ job, label }: { job: Schemas['ArtworkJobOut']; label: string }) {
  const { done = 0, total = 0 } = (job.progress ?? {}) as { done?: number; total?: number }
  return (
    <div className="space-y-1" aria-live="polite">
      <p className="text-xs">
        {label}… <span className="text-muted tabular-nums">{done} / {total || '?'}</span>
      </p>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${total ? (done / total) * 100 : 5}%` }} />
      </div>
    </div>
  )
}

function LogoPacks({ serverId }: { serverId: number }) {
  const jobs = useArtworkJobs(serverId)
  const exportJob = useStartExport(serverId)
  const upload = useUploadPack(serverId)
  const applyPack = useApplyPack(serverId)
  const [chosen, setChosen] = useState<Set<number>>(new Set())
  const latestExport = jobs.data?.find((j) => j.kind === 'export')
  const latestImport = jobs.data?.find((j) => j.kind === 'import')
  const active = (j?: Schemas['ArtworkJobOut']) => j && (j.status === 'queued' || j.status === 'running')
  const preview = upload.data

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    upload.mutate(file, {
      onSuccess: (data) => setChosen(new Set(data.entries.filter((x) => x.match).map((x) => x.index))),
    })
  }

  return (
    <details className="rounded-2xl border border-border bg-surface p-3 text-sm">
      <summary className="cursor-pointer font-medium">Share logo packs</summary>
      <div className="mt-3 space-y-4">
        <div className="space-y-2">
          <p className="text-xs text-muted">
            Export the images of every artist marked Done or Custom as a zip, to back them up or share them. Packs
            match artists by MusicBrainz ID, so they work on other servers too.
          </p>
          {active(latestExport) ? (
            <JobProgress job={latestExport!} label="Exporting" />
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Button busy={exportJob.isPending} onClick={() => exportJob.mutate()}>
                Export
              </Button>
              {latestExport?.download && (
                <a href={latestExport.download} className="text-xs text-accent underline">
                  Download ({String((latestExport.stats as Record<string, number>).exported ?? 0)} artists)
                </a>
              )}
            </div>
          )}
          {exportJob.isError && <Notice tone="error">{errorMessage(exportJob.error)}</Notice>}
        </div>

        <div className="space-y-2 border-t border-border pt-3">
          <label className="block">
            <span className="mb-1 block text-xs font-medium">Import a pack (.zip)</span>
            <input type="file" accept=".zip,application/zip" onChange={onFile} className="text-xs" />
          </label>
          {upload.isPending && <Spinner small />}
          {upload.isError && <Notice tone="error">{errorMessage(upload.error)}</Notice>}
          {active(latestImport) && <JobProgress job={latestImport!} label="Importing" />}
          {preview && !active(latestImport) && (
            <div className="space-y-2">
              <p className="text-xs text-muted">
                {preview.matched} of {preview.total} match your artists. Ticked ones will replace their current image.
              </p>
              <ul className="max-h-72 space-y-1 overflow-y-auto">
                {preview.entries.map((entry) => (
                  <li key={entry.index} className={`flex items-center gap-2 ${entry.match ? '' : 'opacity-50'}`}>
                    <input
                      type="checkbox"
                      aria-label={`Import ${entry.artist_name}`}
                      disabled={!entry.match}
                      checked={chosen.has(entry.index)}
                      onChange={(e) => {
                        const next = new Set(chosen)
                        if (e.target.checked) next.add(entry.index)
                        else next.delete(entry.index)
                        setChosen(next)
                      }}
                    />
                    <img src={entry.image} alt="" className="size-8 rounded bg-[#17181c] object-contain" />
                    <span className="min-w-0 flex-1 truncate text-xs">
                      {entry.artist_name}
                      <span className="block text-muted">
                        {entry.match
                          ? `→ ${entry.match.title} (by ${entry.matched_by === 'mbid' ? 'MusicBrainz ID' : 'name'})`
                          : 'Not in your library'}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
              <Button
                variant="primary"
                disabled={chosen.size === 0}
                busy={applyPack.isPending}
                onClick={() => {
                  const items = preview.entries
                    .filter((x) => x.match && chosen.has(x.index))
                    .map((x) => ({ index: x.index, rating_key: x.match!.rating_key }))
                  applyPack.mutate({ importId: preview.import_id, items }, { onSuccess: () => upload.reset() })
                }}
              >
                Apply {chosen.size}
              </Button>
              {applyPack.isError && <Notice tone="error">{errorMessage(applyPack.error)}</Notice>}
            </div>
          )}
          {latestImport?.status === 'succeeded' && !preview && (
            <p className="text-xs text-muted">
              Last import: {String((latestImport.stats as Record<string, number>).applied ?? 0)} applied.
            </p>
          )}
        </div>
      </div>
    </details>
  )
}
