import { useDeferredValue, useState } from 'react'
import { Link } from 'react-router'
import {
  useAlbumCovers,
  useApplyBest,
  useApplyCover,
  useCoverCandidates,
  useCoverPreview,
  useKeepCover,
  useStartBestCovers,
  type AlbumCover,
  type BestOutcome,
  type BestSource,
  type CoverFilter,
  type CoverSource,
} from '../api/albumCovers'
import { useArtworkJobs, useUploadImage, type SourceSpec } from '../api/artwork'
import { useMe, useSetPreferences } from '../api/hooks'
import { errorMessage } from '../api/client'
import { imageUrl } from '../api/library'
import { FilePicker } from '../components/controls'
import { Artwork } from '../components/library'
import { Zoomable } from '../components/Lightbox'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { formatCount, formatRelative } from '../format'

type Tab = CoverSource | 'upload'

const FILTERS: [CoverFilter, string][] = [
  ['needs', 'Needs a better one'],
  ['missing', 'No cover'],
  ['low', 'Low resolution'],
  ['all', 'All'],
]
const TABS: [Tab, string][] = [
  ['deezer', 'Deezer'],
  ['itunes', 'iTunes'],
  ['coverartarchive', 'Cover Art Archive'],
  ['tidal', 'Tidal'],
  ['fanart', 'fanart.tv'],
  ['plex', 'Plex'],
  ['search', 'Web search'],
  ['upload', 'Upload'],
]
const SOURCE_NAME: Record<string, string> = {
  coverartarchive: 'Cover Art Archive',
  deezer: 'Deezer',
  itunes: 'iTunes',
  tidal: 'Tidal',
  fanart: 'fanart.tv',
  plex: 'Plex',
  search: 'web search',
  upload: 'an upload',
}

const size = (w: number | null | undefined, h: number | null | undefined) => (w && h ? `${w}×${h}` : null)

/** How a cover is doing, for the list and the editor. */
function CoverBadge({ cover }: { cover: AlbumCover }) {
  const [text, tone] =
    cover.state === 'missing'
      ? [cover.thumb ? 'Can’t be read' : 'No cover', 'bg-danger/15 text-danger']
      : cover.state === 'low'
        ? [size(cover.width, cover.height) ?? 'Low', 'bg-warn/15 text-warn']
        : cover.state === 'unchecked'
          ? ['Measuring…', 'text-muted']
          : [size(cover.width, cover.height) ?? 'OK', 'text-muted']
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium tabular-nums ${tone}`}>{cover.kept ? `Kept · ${text}` : text}</span>
}

/** Library → Artwork → Album covers (server owners). */
export function AlbumCovers({ serverId }: { serverId: number }) {
  const [show, setShow] = useState<CoverFilter>('needs')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<AlbumCover | null>(null)
  const q = useDeferredValue(query.trim())
  const albums = useAlbumCovers(serverId, show, q)
  const items = albums.data?.pages.flatMap((p) => p.items) ?? []
  const counts = albums.data?.pages[0]?.counts ?? {}
  const n = (k: string) => counts[k] ?? 0
  const filterCount: Record<CoverFilter, number> = {
    needs: n('missing') + n('low'),
    missing: n('missing'),
    low: n('low'),
    all: n('missing') + n('low') + n('ok') + n('unchecked') + n('kept'),
  }
  const current = selected ? (items.find((a) => a.rating_key === selected.rating_key) ?? selected) : null

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[20rem_1fr]">
      <div className={`space-y-3 ${current ? 'hidden lg:block' : ''}`}>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search albums or artists"
          aria-label="Search albums or artists"
          className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
        />
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show">
          {FILTERS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={show === key}
              onClick={() => setShow(key)}
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                show === key ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted hover:text-text'
              }`}
            >
              {label} <span className="tabular-nums opacity-70">{formatCount(filterCount[key])}</span>
            </button>
          ))}
        </div>
        {show !== 'all' && (albums.data?.pages[0]?.total ?? 0) > 0 && <BestMatches serverId={serverId} show={show} q={q} count={albums.data?.pages[0]?.total ?? 0} />}
        {n('unchecked') > 0 && (
          <p className="flex items-center gap-2 text-xs text-muted">
            <Spinner small /> Measuring {formatCount(n('unchecked'))} covers…
          </p>
        )}
        {albums.isPending ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : albums.isError ? (
          <Notice tone="error">{errorMessage(albums.error)}</Notice>
        ) : (
          <ul className="max-h-[70vh] divide-y divide-border overflow-y-auto rounded-2xl border border-border bg-surface" aria-label="Albums">
            {items.map((a) => (
              <li key={a.rating_key} className={current?.rating_key === a.rating_key ? 'bg-surface-2' : 'hover:bg-surface-2'}>
                <button type="button" onClick={() => setSelected(a)} className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm">
                  <Artwork serverId={serverId} path={a.thumb} title={a.title} size={96} className="size-10 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{a.title}</span>
                    <span className="block truncate text-xs text-muted">{[a.artist, a.year].filter(Boolean).join(' · ')}</span>
                  </span>
                  <CoverBadge cover={a} />
                </button>
              </li>
            ))}
            {albums.hasNextPage && (
              <li className="p-2 text-center">
                <Button variant="ghost" busy={albums.isFetchingNextPage} onClick={() => void albums.fetchNextPage()}>
                  More
                </Button>
              </li>
            )}
            {items.length === 0 && (
              <li className="p-3 text-sm text-muted">
                {show === 'all' || q ? 'No albums match.' : n('unchecked') ? 'None so far: covers are still being measured.' : 'None: every cover looks good.'}
              </li>
            )}
          </ul>
        )}
        <p className="px-1 text-xs text-muted">Low resolution: under 600 pixels on the shorter side.</p>
      </div>
      {current ? (
        <CoverEditor key={current.rating_key} serverId={serverId} album={current} onBack={() => setSelected(null)} onSaved={setSelected} />
      ) : (
        <Card>
          <p className="text-sm text-muted">Pick an album to find it a better cover.</p>
        </Card>
      )}
    </div>
  )
}

function CoverEditor({
  serverId,
  album,
  onBack,
  onSaved,
}: {
  serverId: number
  album: AlbumCover
  onBack: () => void
  onSaved: (album: AlbumCover) => void
}) {
  const me = useMe()
  const savePreferences = useSetPreferences()
  const opensOn = me.data?.preferences?.cover_source ?? 'deezer'
  const [tab, setTab] = useState<Tab>(opensOn)
  const [source, setSource] = useState<SourceSpec | null>(null)
  const [square, setSquare] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const preview = useCoverPreview(serverId, album.rating_key, source, square)
  const apply = useApplyCover(serverId)
  const best = useApplyBest(serverId)
  const [found, setFound] = useState<BestOutcome | null>(null)
  const keep = useKeepCover(serverId)
  const choose = (spec: SourceSpec) => {
    setNotice(null)
    setSource(spec)
  }
  const now = size(album.width, album.height)
  const wide = preview.width && preview.height && preview.width !== preview.height

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onBack} className="text-sm text-muted hover:text-text lg:hidden">
          ← Albums
        </button>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-2xl font-bold">
            <Link to={`/library/albums/${album.rating_key}`} className="hover:underline">
              {album.title}
            </Link>
          </h2>
          <p className="truncate text-sm text-muted">{[album.artist, album.year].filter(Boolean).join(' · ')}</p>
        </div>
        <Button
          variant="secondary"
          busy={best.isPending}
          title="The biggest cover of this very album on Deezer, iTunes, Tidal or the Cover Art Archive, if it’s at least 1000 × 1000"
          onClick={() =>
            best.mutate(album.rating_key, {
              onSuccess: (out) => {
                setFound(out)
                if (out.outcome === 'applied' && out.cover) {
                  setSource(null)
                  onSaved(out.cover)
                }
              },
            })
          }
        >
          Apply best match
        </Button>
        {album.state !== 'ok' && album.state !== 'unchecked' && (
          <Button
            variant="ghost"
            busy={keep.isPending}
            onClick={() => keep.mutate({ key: album.rating_key, kept: !album.kept }, { onSuccess: () => onSaved({ ...album, kept: !album.kept }) })}
          >
            {album.kept ? 'Show it in the list again' : 'It’s fine as it is'}
          </Button>
        )}
      </div>
      {best.isError && <Notice tone="error">{errorMessage(best.error)}</Notice>}
      {found && <Notice>{bestText(found)}</Notice>}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_22rem] xl:items-start">
        <Card>
          <div className="no-scrollbar mb-4 flex gap-1 overflow-x-auto border-b border-border" role="tablist" aria-label="Where from">
            {TABS.map(([key, label]) => (
              // The star: the default source (filled), or make this one the default.
              <span
                key={key}
                role="presentation"
                className={`group flex shrink-0 items-center border-b-2 pb-2 ${tab === key ? 'border-accent' : 'border-transparent'}`}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === key}
                  onClick={() => setTab(key)}
                  className={`pl-3 text-sm font-medium ${key === 'upload' ? 'pr-3' : 'pr-1'} ${tab === key ? 'text-text' : 'text-muted hover:text-text'}`}
                >
                  {label}
                </button>
                {key !== 'upload' &&
                  (key === opensOn ? (
                    <span className="pr-3 text-sm text-accent" title="Default source" aria-label="Default source">
                      ★
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => savePreferences.mutate({ cover_source: key })}
                      title="Set as default source"
                      aria-label={`Set ${label} as default source`}
                      className={`pr-3 text-sm text-muted hover:text-accent focus-visible:opacity-100 ${tab === key ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}
                    >
                      ☆
                    </button>
                  ))}
              </span>
            ))}
          </div>
          {tab === 'upload' ? (
            <CoverUpload serverId={serverId} onUploaded={(id) => choose({ kind: 'upload', upload_id: id })} />
          ) : tab === 'search' ? (
            <WebSearch
              serverId={serverId}
              album={album}
              selected={source?.kind === 'candidate' ? (source.token ?? null) : null}
              onChoose={(token) => choose({ kind: 'candidate', token })}
            />
          ) : (
            <CoverGrid
              serverId={serverId}
              album={album}
              source={tab}
              selected={source?.kind === 'candidate' ? (source.token ?? null) : null}
              onChoose={(token) => choose({ kind: 'candidate', token })}
            />
          )}
        </Card>

        {/* Next to the covers found on a wide screen, and kept in view while scrolling them. */}
        <Card title="Preview" className="xl:sticky xl:top-[4.5rem] xl:max-h-[calc(100dvh-5.5rem)] xl:overflow-y-auto">
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-1">
            <figure className="space-y-1">
              <Zoomable image={album.thumb ? { src: imageUrl(serverId, album.thumb, 1000)!, alt: `Current cover of ${album.title}` } : null}>
                <Artwork serverId={serverId} path={album.thumb} title={album.title} size={1000} className="w-full" />
              </Zoomable>
              <figcaption className="text-center text-sm text-muted">Now{now ? ` · ${now}` : ''}</figcaption>
            </figure>
            <figure className="space-y-1">
              <div className="relative aspect-square overflow-hidden rounded-lg bg-surface-2">
                {preview.url && (
                  <Zoomable image={{ src: preview.url, alt: `New cover for ${album.title}` }} className="size-full">
                    <img src={preview.url} alt={`New cover for ${album.title}`} className="size-full object-contain" />
                  </Zoomable>
                )}
                {!source && (
                  <span className="absolute inset-0 flex items-center justify-center p-2 text-center text-sm text-muted">Choose a cover above</span>
                )}
                {preview.loading && (
                  <span className="absolute inset-0 flex items-center justify-center bg-bg/40">
                    <Spinner />
                  </span>
                )}
              </div>
              <figcaption className="text-center text-sm text-muted">New{preview.width ? ` · ${size(preview.width, preview.height)}` : ''}</figcaption>
            </figure>
          </div>
          {preview.error && (
            <div className="mt-3">
              <Notice tone="error">{preview.error}</Notice>
            </div>
          )}
          {(wide || square) && (
            <label className="mt-3 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={square} onChange={(e) => setSquare(e.target.checked)} className="accent-[var(--color-accent)]" />
              Crop to a square
            </label>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={!source || Boolean(preview.error)}
              busy={apply.isPending}
              onClick={() =>
                source &&
                apply.mutate(
                  { key: album.rating_key, source, square },
                  {
                    onSuccess: (saved) => {
                      setNotice('Saved to Plex.')
                      setSource(null)
                      onSaved(saved)
                    },
                  },
                )
              }
            >
              Save to Plex
            </Button>
          </div>
          {apply.isError && (
            <div className="mt-3">
              <Notice tone="error">{errorMessage(apply.error)}</Notice>
            </div>
          )}
          {notice && <p className="mt-3 text-sm text-ok">{notice}</p>}
          {album.applied_at && (
            <p className="mt-2 text-xs text-muted">
              Last changed {formatRelative(album.applied_at)}
              {album.source && ` (from ${SOURCE_NAME[album.source] ?? album.source})`}.
            </p>
          )}
          <p className="mt-3 text-xs text-muted">Saved to Plex for everyone using the server. Covers over 3000 pixels are scaled down.</p>
        </Card>
      </div>
    </div>
  )
}

const BEST_RULE = 'the same album by the same artist, at least 1000 × 1000'

function bestText(out: BestOutcome): string {
  const found = out.source ? `${SOURCE_NAME[out.source] ?? out.source}, ${size(out.width, out.height)}` : ''
  if (out.outcome === 'applied') return `Saved the best match to Plex (${found}).`
  if (out.outcome === 'already') return `The cover in Plex is already as big as the best match (${found}).`
  return `No match found: nothing on Deezer, iTunes, Tidal or the Cover Art Archive is ${BEST_RULE}.`
}

/** The best match for every album listed, in the background (a few seconds an album). */
const BEST_SOURCES: [BestSource, string][] = [
  ['deezer', 'Deezer'],
  ['itunes', 'iTunes'],
  ['tidal', 'Tidal (when set up)'],
  ['coverartarchive', 'Cover Art Archive'],
]

function BestMatches({ serverId, show, q, count }: { serverId: number; show: Exclude<CoverFilter, 'all'>; q: string; count: number }) {
  const jobs = useArtworkJobs(serverId)
  const start = useStartBestCovers(serverId)
  const me = useMe()
  const savePreferences = useSetPreferences()
  const saved = me.data?.preferences?.best_cover_sources ?? BEST_SOURCES.map(([k]) => k)
  const [chosen, setChosen] = useState<BestSource[] | null>(null)
  const sources = chosen ?? saved
  const [confirming, setConfirming] = useState(false)
  const latest = jobs.data?.find((j) => j.kind === 'covers')
  const running = latest && (latest.status === 'queued' || latest.status === 'running')
  const { done = 0, total = 0 } = (latest?.progress ?? {}) as { done?: number; total?: number }
  const stats = (latest?.stats ?? {}) as Record<string, number>
  return (
    <div className="space-y-2 rounded-2xl border border-border bg-surface p-3 text-sm">
      {running ? (
        <div className="space-y-2" aria-live="polite">
          <p>
            Applying best matches… <span className="tabular-nums text-muted">{done} / {total || '?'}</span>
          </p>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${total ? (done / total) * 100 : 5}%` }} />
          </div>
        </div>
      ) : confirming ? (
        <div className="space-y-2">
          <p>
            Look for a cover for {count === 1 ? 'the album' : `all ${formatCount(count)} albums`} listed that is {BEST_RULE}, and
            save the biggest to Plex? Albums without one are left alone. It takes a few seconds an album.
          </p>
          <fieldset className="space-y-1">
            <legend className="mb-1 text-xs font-medium text-muted">Look on</legend>
            {BEST_SOURCES.map(([key, label]) => (
              <label key={key} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={sources.includes(key)}
                  onChange={(e) =>
                    setChosen(e.target.checked ? BEST_SOURCES.map(([k]) => k).filter((k) => k === key || sources.includes(k)) : sources.filter((k) => k !== key))
                  }
                  className="accent-[var(--color-accent)]"
                />
                {label}
              </label>
            ))}
          </fieldset>
          <div className="flex gap-2">
            <Button
              variant="primary"
              busy={start.isPending}
              disabled={sources.length === 0}
              onClick={() => {
                // Remembered (on the account), and used by "Apply best match" on one album too.
                if (chosen && chosen.join() !== saved.join()) savePreferences.mutate({ best_cover_sources: chosen })
                start.mutate({ show, q: q || undefined, sources }, { onSettled: () => setConfirming(false) })
              }}
            >
              Apply to {formatCount(count)}
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <Button variant="secondary" onClick={() => setConfirming(true)} className="w-full">
            Apply best matches
          </Button>
          {latest?.status === 'succeeded' && (
            <p className="text-xs text-muted">
              Last run: {stats.applied ?? 0} applied
              {stats.not_found ? `, ${stats.not_found} without a match` : ''}
              {stats.already ? `, ${stats.already} already as big` : ''}
              {stats.failed ? `, ${stats.failed} failed` : ''}.
            </p>
          )}
          {latest?.status === 'failed' && <p className="text-xs text-danger">Last run failed: {latest.error}</p>}
        </>
      )}
      {start.isError && <Notice tone="error">{errorMessage(start.error)}</Notice>}
    </div>
  )
}

function CoverGrid({
  serverId,
  album,
  source,
  query = '',
  selected,
  onChoose,
}: {
  serverId: number
  album: AlbumCover
  source: CoverSource
  query?: string
  selected: string | null
  onChoose: (token: string) => void
}) {
  const found = useCoverCandidates(serverId, album.rating_key, source, query)
  if (found.isPending)
    return (
      <div className="flex min-h-56 flex-col items-center justify-center gap-3 text-sm text-muted">
        <Spinner large />
        <p>{source === 'search' ? 'Searching the web…' : `Looking on ${source === 'coverartarchive' ? 'the Cover Art Archive' : SOURCE_NAME[source]}…`}</p>
      </div>
    )
  if (found.isError) return <Notice tone="error">{errorMessage(found.error)}</Notice>
  const { candidates, note } = found.data
  return (
    <div className="space-y-3">
      {note && <Notice>{note}</Notice>}
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 2xl:grid-cols-4" aria-label="Covers found">
        {candidates.map((c) => {
          const dims = size(c.width, c.height) ?? (c.up_to ? `up to ${c.up_to} px` : null)
          return (
            <li key={c.token}>
              <button
                type="button"
                onClick={() => onChoose(c.token)}
                aria-pressed={selected === c.token}
                className={`block w-full rounded-lg border-2 p-1 text-left ${selected === c.token ? 'border-accent' : 'border-transparent hover:border-border'}`}
              >
                <div className="aspect-square overflow-hidden rounded bg-surface-2">
                  <img src={c.thumb} alt="" loading="lazy" className="size-full object-cover" />
                </div>
                <span className="mt-1 block truncate text-xs" title={c.label ?? undefined}>
                  {c.label ?? 'Cover'}
                </span>
                {dims && <span className="block text-[11px] tabular-nums text-muted">{dims}</span>}
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function WebSearch({
  serverId,
  album,
  selected,
  onChoose,
}: {
  serverId: number
  album: AlbumCover
  selected: string | null
  onChoose: (token: string) => void
}) {
  const start = `${album.artist ?? ''} ${album.title} album cover`.trim()
  const [text, setText] = useState(start)
  const [query, setQuery] = useState(start)
  return (
    <div className="space-y-3">
      <form
        className="flex gap-2"
        role="search"
        onSubmit={(e) => {
          e.preventDefault()
          if (text.trim()) setQuery(text.trim())
        }}
      >
        <input
          aria-label="Search the web for a cover"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
        />
        <Button type="submit" variant="secondary" disabled={!text.trim()} className="shrink-0">
          Search
        </Button>
      </form>
      <CoverGrid serverId={serverId} album={album} source="search" query={query === start ? '' : query} selected={selected} onChoose={onChoose} />
    </div>
  )
}

function CoverUpload({ serverId, onUploaded }: { serverId: number; onUploaded: (id: string) => void }) {
  const upload = useUploadImage(serverId)
  return (
    <div className="space-y-3">
      <FilePicker
        accept="image/png,image/jpeg,image/webp,image/gif"
        label="An image file"
        hint="PNG, JPEG, WebP or GIF, up to 15 MB."
        onFile={(file) => upload.mutate(file, { onSuccess: (r) => onUploaded(r.upload_id) })}
      />
      {upload.isPending && <Spinner small />}
      {upload.isError && <Notice tone="error">{errorMessage(upload.error)}</Notice>}
    </div>
  )
}
