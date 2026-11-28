import { useDeferredValue, useId, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { imageUrl } from '../api/library'
import { Lightbox, Zoomable } from '../components/Lightbox'
import { ColorPicker, FilePicker, Slider } from '../components/controls'
import { Select } from '../components/Select'
import { ResultStrip } from '../components/ResultStrip'
import { Tooltip } from '../components/Tooltip'
import { Link, useLocation } from 'react-router'
import {
  artworkKeys,
  useApplyArtwork,
  useArtworkArtists,
  useArtworkJobs,
  useBulkArtworkStatus,
  useCandidates,
  useFonts,
  usePreview,
  useSetArtworkStatus,
  useStartFanartJob,
  useStartTextJob,
  useUploadImage,
  type ArtistSort,
  type ArtworkStatus,
  type CandidateSource,
  type SourceSpec,
  type Transform,
} from '../api/artwork'
import { errorMessage, type Schemas } from '../api/client'
import { useMe } from '../api/hooks'
import { Artwork } from '../components/library'
import { ArtworkJobStop } from '../components/ArtworkJobStatus'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { useLibraryServer } from '../components/useLibraryServer'
import { TabNav } from '../components/TabNav'
import { AlbumCovers } from './AlbumCoversPage'
import { useFillHeight } from '../fillHeight'
import { formatCount, formatRelative } from '../format'
import { useRangeSelection } from '../selection'
import { Segmented } from '../components/Segmented'

type Artist = Schemas['ArtworkArtistOut']
type Tab = CandidateSource | 'upload' | 'text'

const STATUSES: [ArtworkStatus, string][] = [
  ['none', 'To do'],
  ['done', 'Done'],
  ['custom', 'Custom'],
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

/** Library → Artwork: artist images and album covers, each its own sub-tab. */
export function ArtworkPage() {
  const { active, blocker } = useLibraryServer()
  const me = useMe()
  const albums = useLocation().pathname.replace(/\/$/, '').endsWith('/artwork/albums')
  return (
    <>
      <TabNav
        label="Artwork"
        tabs={[
          { to: '/library/artwork', label: 'Artist images', end: true },
          { to: '/library/artwork/albums', label: 'Album covers' },
        ]}
      />
      <p className="mb-4 text-sm text-muted">
        {albums
          ? 'Find albums without a cover or with a blurry one, and replace them from Deezer, iTunes, the Cover Art Archive, Tidal, fanart.tv or the web. '
          : 'Replace artist photos with logos. '}
        Changes are saved to Plex for everyone using the server.
      </p>
      {blocker ??
        (!active!.is_owner ? (
          <Notice>
            Only the owner of {active!.name} can change {albums ? 'album covers' : 'artist images'}.
          </Notice>
        ) : albums ? (
          <AlbumCovers serverId={active!.id} />
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
  const [sort, setSortState] = useState<ArtistSort>(() => ((['added', 'changed'] as const).find((s) => s === localStorage.getItem('artwork-sort')) ?? 'name'))
  const setSort = (v: ArtistSort) => {
    localStorage.setItem('artwork-sort', v)
    setSortState(v)
  }
  const q = useDeferredValue(query.trim())
  const artists = useArtworkArtists(serverId, q, status, sort)
  const { ref: fillRef, style: fillStyle } = useFillHeight<HTMLDivElement>()
  const items = artists.data?.pages.flatMap((p) => p.items) ?? []
  const counts = artists.data?.pages[0]?.counts ?? {}
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  const ticked = useRangeSelection<number>()
  const order = items.map((a) => a.rating_key)
  // Keep the selected artist in step with the list (status / thumb after applying).
  const current = selected ? (items.find((a) => a.rating_key === selected.rating_key) ?? selected) : null

  return (
    // The artists down the left third; the preview and (glued to the bottom of the window)
    // the images found on the rest.
    <div
      ref={fillRef}
      style={fillStyle}
      className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] lg:grid-rows-[minmax(0,1fr)]"
    >
      <section
        aria-label="Artists"
        className={`min-h-0 flex-col gap-3 rounded-2xl border border-border bg-surface p-3 ${current ? 'hidden lg:flex' : 'flex'}`}
      >
        <BulkLogos
          serverId={serverId}
          selected={[...ticked.selected]}
          listed={{ status: status ?? 'all', q, total: artists.data?.pages[0]?.total ?? 0 }}
          onStarted={ticked.clear}
        />
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
        <label className="flex items-center justify-between gap-2 text-xs text-muted">
          Sort
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as ArtistSort)}
            className="rounded-lg border border-border bg-surface-2 px-2 py-1 text-xs text-text"
          >
            <option value="name">Name</option>
            <option value="added">Recently added</option>
            <option value="changed">Recently changed</option>
          </select>
        </label>
        <BulkStatus serverId={serverId} todo={counts.none ?? 0} />
        {artists.isPending ? (
          <Spinner small />
        ) : artists.isError ? (
          <Notice tone="error">{errorMessage(artists.error)}</Notice>
        ) : (
          <ul
            aria-label="Artists"
            className="max-h-[70vh] min-h-0 flex-1 divide-y divide-border overflow-y-auto border-t border-border lg:max-h-none"
          >
            {items.map((a) => (
              <li
                key={a.rating_key}
                className={`flex items-center hover:bg-surface-2 ${current?.rating_key === a.rating_key ? 'bg-surface-2' : ''}`}
              >
                <input
                  type="checkbox"
                  aria-label={`Select ${a.title}`}
                  title="Select (shift-click: a range)"
                  checked={ticked.selected.has(a.rating_key)}
                  onChange={() => undefined}
                  onClick={(e) => ticked.toggle(a.rating_key, order, e.shiftKey)}
                  className="ml-2 shrink-0"
                />
                <button
                  type="button"
                  onClick={() => setSelected(a)}
                  className="flex min-w-0 flex-1 items-center gap-3 py-2 pl-3 text-left text-sm"
                >
                  <Artwork serverId={serverId} path={a.thumb} title={a.title} size={96} round className="size-9 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{a.title}</span>
                    {sort === 'added' && a.added_at && (
                      <span className="block truncate text-xs text-muted">Added {formatRelative(a.added_at)}</span>
                    )}
                    {sort === 'changed' && a.applied_at && (
                      <span className="block truncate text-xs text-muted">Changed {formatRelative(a.applied_at)}</span>
                    )}
                  </span>
                </button>
                <StatusDot serverId={serverId} artist={a} />
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
        <p className="px-1 text-xs text-muted">
          Saving to artist folders, logo packs and the text logo fonts are in{' '}
          <Link to="/settings/artwork" className="underline hover:text-text">
            Settings → Artwork
          </Link>{' '}
          and{' '}
          <Link to="/settings/export/logos" className="underline hover:text-text">
            Export &amp; import
          </Link>
          .
        </p>
      </section>
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
  const qc = useQueryClient()
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
    <div className="flex min-h-0 min-w-0 flex-col gap-4">
      {/* The artist, the preview and the adjustments: all the room above the results. */}
      <section aria-label="Preview" className="flex min-h-0 flex-1 flex-col gap-3 rounded-2xl border border-border bg-surface p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={onBack} className="text-sm text-muted hover:text-text lg:hidden">
            ← Artists
          </button>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-2xl font-bold">
              <Link to={`/library/artists/${artist.rating_key}`} className="hover:underline">
                {artist.title}
              </Link>
            </h2>
            {/* Written by the worker a moment after saving. */}
            {artist.folder_error ? (
              <p className="truncate text-xs text-danger">artist.jpg not saved: {artist.folder_error}</p>
            ) : artist.folder ? (
              <p className="truncate text-xs text-muted">
                artist.jpg saved
                {artist.folder_saved_at ? ` ${formatRelative(artist.folder_saved_at)}` : ''} in{' '}
                {artist.folder.replace(/\/artist\.jpg$/, '')}
              </p>
            ) : null}
          </div>
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

        <div className="flex min-h-0 flex-1 flex-col gap-4 xl:flex-row">
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            {/* As big as fits: two squares side by side in the room the preview has. */}
            <div className="flex min-h-0 flex-1 justify-center gap-6 lg:[container-type:size]">
              <Figure label="Now">
                <Zoomable
                  image={
                    artist.thumb
                      ? {
                          src: imageUrl(serverId, artist.thumb, 1000)!,
                          alt: `Current image for ${artist.title}`,
                        }
                      : null
                  }
                >
                  <Artwork serverId={serverId} path={artist.thumb} title={artist.title} size={400} className="w-full" />
                </Zoomable>
              </Figure>
              <Figure label="New">
                <div className="relative aspect-square overflow-hidden rounded-lg bg-surface-2">
                  {preview.url && (
                    <Zoomable
                      image={{
                        src: preview.url,
                        alt: `New image for ${artist.title}`,
                      }}
                      className="size-full"
                    >
                      <img src={preview.url} alt={`New image for ${artist.title}`} className="size-full object-cover" />
                    </Zoomable>
                  )}
                  {!source && (
                    <span className="absolute inset-0 flex items-center justify-center p-2 text-center text-xs text-muted">
                      Choose an image below
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
                      onSuccess: (result) => {
                        // The worker writes artist.jpg: look again for how it went.
                        if (result.folder_queued)
                          window.setTimeout(
                            () =>
                              void qc.invalidateQueries({
                                queryKey: artworkKeys.all(serverId),
                              }),
                            3000,
                          )
                        setNotice(
                          result.folder_queued
                            ? 'Saved to Plex. artist.jpg is being written to the artist folder.'
                            : result.folder
                            ? `Saved to Plex and to ${result.folder}.`
                            : result.folder_error
                              ? `Saved to Plex, but not to the artist folder: ${result.folder_error}`
                              : 'Saved to Plex.',
                        )
                      },
                    },
                  )
                }
              >
                Save to Plex
              </Button>
              <Tooltip content="Adjust the image Plex has now">
                <Button variant="ghost" onClick={() => choose({ kind: 'current' })}>
                  Edit current
                </Button>
              </Tooltip>
            </div>
            {apply.isError && <div className="mt-3"><Notice tone="error">{errorMessage(apply.error)}</Notice></div>}
            {notice && <p className="mt-3 text-sm text-ok">{notice}</p>}
            {artist.applied_at && (
              <p className="mt-2 text-xs text-muted">
                Last changed {formatRelative(artist.applied_at)}
                {artist.source && ` (${artist.source})`}.
              </p>
            )}
          </div>
          <div className="min-h-0 xl:w-72 xl:overflow-y-auto">
            <Adjustments transform={transform} onChange={patch} onReset={() => setTransform(DEFAULT_TRANSFORM)} />
          </div>
        </div>
      </section>

      {/* The images found: one row, glued to the bottom of the window, scrolling sideways. */}
      <section
        aria-label="Where from"
        className="flex h-[17rem] shrink-0 flex-col rounded-2xl border border-border bg-surface px-3 pt-2 pb-3"
      >
        <div className="mb-3 flex gap-1 overflow-x-auto border-b border-border" role="tablist">
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
        {/* Upload and Text are forms: they scroll in the row when they don't fit. */}
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
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
        </div>
      </section>
    </div>
  )
}

function Figure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <figure className="w-1/2 space-y-1 lg:w-[min(calc(100cqh-1.5rem),calc(50cqw-0.75rem))]">
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
  if (found.isPending)
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    )
  if (found.isError) return <Notice tone="error">{errorMessage(found.error)}</Notice>
  const { candidates, note } = found.data
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
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
      {candidates.length === 0 && !note && <p className="text-sm text-muted">None found here.</p>}
      <ResultStrip label="Images found">
        {candidates.map((c) => (
          <li key={c.token} className="relative shrink-0">
            <button
              type="button"
              onClick={() => onChoose(c.token)}
              aria-pressed={selected === c.token}
              className={`block w-56 rounded-lg border-2 p-1 text-left ${
                selected === c.token ? 'border-accent' : 'border-transparent hover:border-border'
              }`}
            >
              {/* Logos are often transparent: show them on a checkerboard-free dark plate. */}
              <div className="flex h-36 items-center justify-center rounded bg-[#17181c]">
                <img src={c.thumb} alt="" loading="lazy" className="max-h-full max-w-full object-contain" />
              </div>
              <span className="mt-1 block truncate text-xs text-muted">
                {c.label}
                {c.width && c.height ? ` · ${c.width}×${c.height}` : ''}
              </span>
            </button>
            <ZoomCandidate src={`${c.thumb}&size=600`} label={c.label ?? 'Candidate'} />
          </li>
        ))}
      </ResultStrip>
    </div>
  )
}

function UploadTab({ serverId, onUploaded }: { serverId: number; onUploaded: (id: string) => void }) {
  const upload = useUploadImage(serverId)
  return (
    <div className="space-y-3">
      <FilePicker
        label="Choose an image"
        accept="image/png,image/jpeg,image/webp,image/gif"
        onFile={(file) =>
          upload.mutate(file, {
            onSuccess: (data) => onUploaded(data.upload_id),
          })
        }
      />
      <p className="text-xs text-muted">PNG with transparency works best for logos. Up to 15 MB.</p>
      {upload.isPending && <Spinner small />}
      {upload.isError && <Notice tone="error">{errorMessage(upload.error)}</Notice>}
      {upload.isSuccess && <p className="text-sm text-ok">Uploaded. Adjust it on the right.</p>}
    </div>
  )
}

function TextTab({ artist, onChange }: { artist: Artist; onChange: (text: Schemas['TextLogo']) => void }) {
  const fonts = useFonts()
  const ids = useId()
  const [spec, setSpec] = useState<Schemas['TextLogo']>({
    text: artist.title,
    font: 'Anton',
    rows: 1,
    color: '#ffffff',
    case: 'none',
  })
  // Admins choose the fonts: fall back to the first one if the default isn't offered.
  const offered = (fonts.data ?? []).flatMap((g) => g.fonts)
  const font = offered.length === 0 || offered.includes(spec.font) ? spec.font : offered[0]
  const update = (p: Partial<Schemas['TextLogo']>) => {
    const next = { ...spec, font, ...p }
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
      <div>
        <span id={`${ids}-font`} className="mb-1 block text-sm font-medium">Font</span>
        <Select
          labelledBy={`${ids}-font`}
          className="w-full"
          value={font}
          onChange={(f) => update({ font: f })}
          options={(fonts.data ?? []).flatMap((g) => g.fonts.map((f) => ({ value: f, label: f, group: g.name })))}
        />
      </div>
      <div>
        <span id={`${ids}-rows`} className="mb-1 block text-sm font-medium">Lines</span>
        <Select
          labelledBy={`${ids}-rows`}
          className="w-full"
          value={spec.rows ?? 1}
          onChange={(rows) => update({ rows })}
          options={[1, 2, 3, 4].map((n) => ({ value: n, label: String(n) }))}
        />
      </div>
      <div>
        <span id={`${ids}-case`} className="mb-1 block text-sm font-medium">Case</span>
        <Select
          labelledBy={`${ids}-case`}
          className="w-full"
          value={spec.case ?? 'none'}
          onChange={(c) => update({ case: c })}
          options={[
            { value: 'none' as const, label: 'As written' },
            { value: 'upper' as const, label: 'UPPER CASE' },
            { value: 'lower' as const, label: 'lower case' },
          ]}
        />
      </div>
      <div>
        <span className="mb-1 block text-sm font-medium">Colour</span>
        <ColorPicker label="Text colour" value={spec.color ?? '#ffffff'} onChange={(color) => update({ color })} />
      </div>
      <div className="sm:col-span-2">
        <button type="button" className="text-sm text-accent" onClick={() => update({})}>
          Preview this text
        </button>
        <p className="mt-1 text-xs text-muted">Fonts are downloaded from Google Fonts the first time they're used.</p>
      </div>
    </div>
  )
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
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
    <div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">Adjust</h3>
        <button type="button" className="text-xs text-muted hover:text-text" onClick={onReset}>
          Reset
        </button>
      </div>
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
            <span className="flex items-center gap-2">
              Tint
              <ColorPicker label="Tint colour" value={transform.tint} onChange={(tint) => onChange({ tint })} />
            </span>
          )}
          <span className="flex items-center gap-2">
            Background
            <ColorPicker label="Background colour" value={transform.background ?? '#000000'} onChange={(background) => onChange({ background })} />
          </span>
        </div>
      </div>
    </div>
  )
}

type Listed = { status: ArtworkStatus | 'all'; q: string; total: number }

/** Logos for many artists at once: the ticked ones, else every one listed (the status
 * filter and search). From fanart.tv (each one's most-liked logo), or text logos of their
 * names: one font and colour for all, or a random pick for each from several. Runs as an
 * artwork job that can be stopped, or cancelled to put the old pictures back. */
function BulkLogos({
  serverId,
  selected,
  listed,
  onStarted,
}: {
  serverId: number
  selected: number[]
  listed: Listed
  onStarted: () => void
}) {
  const jobs = useArtworkJobs(serverId)
  const fanart = useStartFanartJob(serverId)
  const textJob = useStartTextJob(serverId)
  const fonts = useFonts()
  const [open, setOpen] = useState(false)
  const [source, setSource] = useState<'fanart' | 'text'>('fanart')
  const [chosenFonts, setChosenFonts] = useState<string[]>([])
  const [colours, setColours] = useState<string[]>(['#ffffff'])
  const [nextColour, setNextColour] = useState('#f2ad45')
  const [letterCase, setLetterCase] = useState<'none' | 'upper' | 'lower'>('none')
  const latest = jobs.data?.find((j) => j.kind === 'fanart' || j.kind === 'text')
  const running = latest && (latest.status === 'queued' || latest.status === 'running')
  const { done = 0, total = 0 } = (latest?.progress ?? {}) as { done?: number; total?: number }
  const stats = (latest?.stats ?? {}) as Record<string, number>
  const count = selected.length || listed.total
  const scope = selected.length
    ? { rating_keys: selected, status: 'all' as const, q: null }
    : { rating_keys: null, status: listed.status, q: listed.q || null }
  const offered = (fonts.data ?? []).flatMap((g) => g.fonts)
  const start = () => {
    const done = {
      onSuccess: () => {
        setOpen(false)
        onStarted()
      },
    }
    if (source === 'fanart') fanart.mutate(scope, done)
    else textJob.mutate({ ...scope, fonts: chosenFonts, colours, case: letterCase }, done)
  }
  const error = fanart.error ?? textJob.error
  const who = selected.length ? `the ${formatCount(count)} ticked` : `the ${formatCount(count)} listed`

  if (running) {
    return (
      <div className="space-y-2 rounded-2xl border border-border bg-surface p-3 text-sm" aria-live="polite">
        <p>
          {latest.kind === 'text' ? 'Making text logos…' : 'Applying fanart.tv logos…'}{' '}
          <span className="text-muted tabular-nums">{done} / {total || '?'}</span>
        </p>
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
          <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${total ? (done / total) * 100 : 5}%` }} />
        </div>
        <ArtworkJobStop job={latest} />
      </div>
    )
  }
  return (
    <div className="space-y-2 rounded-2xl border border-border bg-surface p-3 text-sm">
      {!open ? (
        <>
          <Button variant="secondary" disabled={count === 0} onClick={() => setOpen(true)} className="w-full">
            Logos for {who} {count === 1 ? 'artist' : 'artists'}…
          </Button>
          {latest?.status === 'succeeded' && (
            <p className="text-xs text-muted">
              Last run: {stats.applied ?? 0} applied
              {stats.no_logo ? `, ${stats.no_logo} without a logo` : ''}
              {stats.no_mbid ? `, ${stats.no_mbid} not on MusicBrainz` : ''}
              {stats.failed ? `, ${stats.failed} failed` : ''}.
            </p>
          )}
          {latest?.status === 'failed' && (
            <p className={`text-xs ${latest.stop_requested ? 'text-muted' : 'text-danger'}`}>
              {latest.stop_requested ? `Last run: ${latest.error}` : `Last run failed: ${latest.error}`}
            </p>
          )}
        </>
      ) : (
        <div className="space-y-3">
          <p className="font-medium">
            Logos for {who} {count === 1 ? 'artist' : 'artists'}
          </p>
          <Segmented
            label="Where from"
            value={source}
            onChange={setSource}
            options={[
              ['fanart', 'fanart.tv'],
              ['text', 'Text logos'],
            ]}
            className="w-full"
          />
          {source === 'fanart' ? (
            <p className="text-xs text-muted">Each one’s most-liked fanart.tv logo. Artists without one are left alone.</p>
          ) : (
            <>
              <fieldset>
                <legend className="mb-1 text-xs font-medium">Fonts</legend>
                <div className="max-h-36 space-y-0.5 overflow-y-auto rounded-lg border border-border p-2 text-xs">
                  {offered.map((f) => (
                    <label key={f} className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={chosenFonts.includes(f)}
                        onChange={(e) => setChosenFonts((c) => (e.target.checked ? [...c, f] : c.filter((x) => x !== f)))}
                      />
                      {f}
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset>
                <legend className="mb-1 text-xs font-medium">Colours</legend>
                <div className="flex flex-wrap items-center gap-1.5">
                  {colours.map((c) => (
                    <button
                      key={c}
                      type="button"
                      aria-label={`Remove ${c}`}
                      title={`Remove ${c}`}
                      disabled={colours.length === 1}
                      onClick={() => setColours((cs) => cs.filter((x) => x !== c))}
                      className="size-6 rounded-full ring-1 ring-border disabled:cursor-default"
                      style={{ background: c }}
                    />
                  ))}
                  <ColorPicker label="Colour to add" value={nextColour} onChange={setNextColour} />
                  <Button
                    variant="ghost"
                    disabled={colours.includes(nextColour) || colours.length >= 12}
                    onClick={() => setColours((cs) => [...cs, nextColour])}
                  >
                    Add
                  </Button>
                </div>
              </fieldset>
              <div className="flex items-center gap-2 text-xs">
                <span className="font-medium">Case</span>
                <Select
                  size="sm"
                  label="Case"
                  value={letterCase}
                  onChange={setLetterCase}
                  options={[
                    { value: 'none' as const, label: 'As written' },
                    { value: 'upper' as const, label: 'UPPER CASE' },
                    { value: 'lower' as const, label: 'lower case' },
                  ]}
                />
              </div>
              <p className="text-xs text-muted">
                {chosenFonts.length > 1 || colours.length > 1
                  ? 'Each artist gets a random pick of these fonts and colours.'
                  : 'Every artist gets the same font and colour.'}
              </p>
            </>
          )}
          <div className="flex gap-2">
            <Button
              variant="primary"
              disabled={source === 'text' && chosenFonts.length === 0}
              busy={fanart.isPending || textJob.isPending}
              onClick={start}
            >
              Apply to {formatCount(count)}
            </Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
          <p className="text-xs text-muted">Saved to Plex as the artists’ pictures; the whole run can be cancelled to put the old ones back.</p>
        </div>
      )}
      {error && <Notice tone="error">{errorMessage(error)}</Notice>}
    </div>
  )
}

/** Mark every "To do" artist as Done or Custom at once. Images are left as they are. */
function BulkStatus({ serverId, todo }: { serverId: number; todo: number }) {
  const mark = useBulkArtworkStatus(serverId)
  const [confirming, setConfirming] = useState<'done' | 'custom' | null>(null)
  const label = (status: ArtworkStatus) => STATUSES.find(([k]) => k === status)?.[1] ?? status
  const marked = mark.isSuccess && !confirming ? mark.data : null

  if (todo === 0 && !marked && !mark.isError) return null
  return (
    <div className="space-y-1.5 text-xs">
      {confirming ? (
        <div className="space-y-2 rounded-2xl border border-border bg-surface p-3 text-sm">
          <p>
            Mark all {todo} "To do" artists as {label(confirming)}? Their images stay as they are.
          </p>
          <div className="flex gap-2">
            <Button
              variant="primary"
              busy={mark.isPending}
              onClick={() =>
                mark.mutate(confirming, {
                  onSettled: () => setConfirming(null),
                })
              }
            >
              Mark {todo} as {label(confirming)}
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        todo > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 text-muted">
            <span>Mark all "To do" as</span>
            {(['done', 'custom'] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setConfirming(key)}
                className="rounded-full bg-surface-2 px-2.5 py-0.5 font-medium hover:text-text"
              >
                {label(key)}
              </button>
            ))}
          </div>
        )
      )}
      {marked && (
        <p className="text-muted" aria-live="polite">
          Marked {marked.changed} {marked.changed === 1 ? 'artist' : 'artists'} as {label(mark.variables!)}.
        </p>
      )}
      {mark.isError && <Notice tone="error">{errorMessage(mark.error)}</Notice>}
    </div>
  )
}

const NEXT_STATUS: Record<ArtworkStatus, ArtworkStatus> = {
  none: 'done',
  done: 'custom',
  custom: 'none',
}

/** The list's status dot: click to cycle To do → Done → Custom. */
function StatusDot({ serverId, artist }: { serverId: number; artist: Artist }) {
  const setStatus = useSetArtworkStatus(serverId)
  const label = (status: ArtworkStatus) => STATUSES.find(([k]) => k === status)?.[1] ?? status
  const pending = setStatus.isPending ? setStatus.variables?.status : undefined
  const shown = pending ?? artist.status
  return (
    <button
      type="button"
      onClick={() => setStatus.mutate({ key: artist.rating_key, status: NEXT_STATUS[shown] })}
      aria-label={`${artist.title}: ${label(shown)}. Change to ${label(NEXT_STATUS[shown])}`}
      className="flex size-9 shrink-0 items-center justify-center rounded-full hover:bg-border"
    >
      <span className={`size-2.5 rounded-full ${STATUS_DOT[shown]}`} />
    </button>
  )
}

/** A small "show larger" button over a candidate (the card itself selects it). */
function ZoomCandidate({ src, label }: { src: string; label: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Show ${label} larger`}
        className="absolute right-2 top-2 flex size-7 items-center justify-center rounded-md bg-black/60 text-sm text-white opacity-70 hover:opacity-100"
      >
        ⤢
      </button>
      {open && <Lightbox images={[{ src, alt: label, caption: label }]} onClose={() => setOpen(false)} />}
    </>
  )
}
