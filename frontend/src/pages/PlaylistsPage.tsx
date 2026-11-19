import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useTags } from '../api/library'
import {
  fetchTagArtists,
  useDeletePlaylist,
  usePlaylist,
  usePlaylists,
  usePlaylistSearch,
  usePreview,
  useRebuildPlaylist,
  useSavePlaylist,
  useSuggestions,
  type Definition,
  type Playlist,
  type PlaylistDetail,
  type Schedule,
  type SeedArtist,
  type SeedTrack,
} from '../api/playlists'
import { Dialog } from '../components/Dialog'
import { Artwork } from '../components/library'
import { Segmented } from '../components/Segmented'
import { Select } from '../components/Select'
import { StarRating } from '../components/StarRating'
import { Tooltip } from '../components/Tooltip'
import { PlayButton } from '../components/PlayButton'
import { Badge, Button, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { useLibraryServer } from '../components/useLibraryServer'
import { formatCount, formatDuration, formatListeningTime, formatRelative } from '../format'

type Order = Definition['order']
type Op = 'AND' | 'OR'

const SCHEDULES: { value: Schedule; label: string }[] = [
  { value: 'manual', label: 'Only when I rebuild it' },
  { value: 'daily', label: 'Refresh daily' },
  { value: 'weekly', label: 'Refresh weekly' },
  { value: 'monthly', label: 'Refresh monthly' },
]
const LIMITS = [25, 50, 100, 200, 500, 1000, 0]
const TOP_PERCENT = [0, 10, 25, 50, 75]

/** The value, once it has stopped changing for `ms`. */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return settled
}

function scheduleText(schedule: Schedule) {
  return { manual: 'Rebuilt by hand', daily: 'Refreshed daily', weekly: 'Refreshed weekly', monthly: 'Refreshed monthly' }[
    schedule
  ]
}

// --- list ------------------------------------------------------------------------------------

export function PlaylistsPage() {
  const { active, blocker } = useLibraryServer()
  return (
    <>
      <PageTitle subtitle="Playlists built from artists, songs and genres, saved to Plex under your own account.">
        Playlists
      </PageTitle>
      {blocker ?? <PlaylistList serverId={active!.id} />}
    </>
  )
}

function StatusBadge({ playlist }: { playlist: Playlist }) {
  if (playlist.status === 'queued' || playlist.status === 'running')
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted">
        <Spinner small /> Building in Plex…
      </span>
    )
  if (playlist.status === 'error') return <Badge>Needs attention</Badge>
  if (playlist.status === 'ok') return <Badge tone="ok">In Plex</Badge>
  return <Badge>Not built yet</Badge>
}

function PlaylistList({ serverId }: { serverId: number }) {
  const playlists = usePlaylists(serverId)
  const rebuild = useRebuildPlaylist(serverId)
  const [deleting, setDeleting] = useState<Playlist | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (playlists.isPending) return <Spinner />
  if (playlists.isError) return <Notice tone="error">{errorMessage(playlists.error)}</Notice>

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted">
          {playlists.data.length === 0 ? '' : `${formatCount(playlists.data.length)} playlists`}
        </p>
        <Link
          to="/playlists/new"
          className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-on-accent hover:bg-accent-strong"
        >
          + New playlist
        </Link>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {playlists.data.length === 0 ? (
        <Card>
          <div className="space-y-2 text-sm">
            <p className="font-semibold">No playlists yet</p>
            <p className="text-muted">
              Start from a few artists, songs or a genre, add similar artists, then keep only what you rate highly or each
              artist's best-known songs. Playlists are saved to Plex, so they show up in Plexamp, and can refresh
              themselves daily, weekly or monthly.
            </p>
          </div>
        </Card>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {playlists.data.map((p) => {
            const result = p.last_result as { count?: number; duration_ms?: number; top_artists?: string[] }
            return (
              <li key={p.id}>
                <Card className="h-full">
                  <div className="flex h-full flex-col gap-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h2 className="truncate font-semibold">{p.name}</h2>
                        {p.description && <p className="line-clamp-2 text-sm text-muted">{p.description}</p>}
                      </div>
                      <StatusBadge playlist={p} />
                    </div>
                    {result.count !== undefined && (
                      <p className="text-sm">
                        {formatCount(result.count)} songs · {formatListeningTime(result.duration_ms ?? 0)}
                        {result.top_artists?.length ? (
                          <span className="text-muted"> · {result.top_artists.slice(0, 3).join(', ')}</span>
                        ) : null}
                      </p>
                    )}
                    {p.status === 'error' && p.error && <Notice tone="warn">{p.error}</Notice>}
                    <p className="text-xs text-muted">
                      {scheduleText(p.schedule)}
                      {p.last_built_at && ` · last built ${formatRelative(p.last_built_at)}`}
                    </p>
                    {p.exportable && (
                      <p className="text-xs text-muted">
                        Download as M3U8:{' '}
                        <a
                          href={`/api/servers/${serverId}/playlists/${p.id}/export.m3u8`}
                          download
                          className="text-accent underline"
                        >
                          Plex's file paths
                        </a>
                        {' · '}
                        <Tooltip content="Paths relative to your music folder: save the file in that folder.">
                          <a
                            href={`/api/servers/${serverId}/playlists/${p.id}/export.m3u8?paths=relative`}
                            download
                            className="text-accent underline"
                          >
                            relative paths
                          </a>
                        </Tooltip>
                      </p>
                    )}
                    <div className="mt-auto flex flex-wrap gap-2">
                      <Link
                        to={`/playlists/${p.id}`}
                        className="inline-flex items-center rounded-lg bg-surface-2 px-4 py-2 text-sm font-semibold hover:bg-border"
                      >
                        Edit
                      </Link>
                      <Button
                        variant="ghost"
                        disabled={p.status === 'queued' || p.status === 'running'}
                        busy={rebuild.isPending && rebuild.variables === p.id}
                        onClick={() => {
                          setError(null)
                          rebuild.mutate(p.id, { onError: (e) => setError(errorMessage(e)) })
                        }}
                      >
                        Rebuild now
                      </Button>
                      <Button variant="danger" className="ml-auto" onClick={() => setDeleting(p)}>
                        Delete
                      </Button>
                    </div>
                  </div>
                </Card>
              </li>
            )
          })}
        </ul>
      )}
      {deleting && <DeleteDialog serverId={serverId} playlist={deleting} onClose={() => setDeleting(null)} />}
    </div>
  )
}

function DeleteDialog({ serverId, playlist, onClose }: { serverId: number; playlist: Playlist; onClose: () => void }) {
  const remove = useDeletePlaylist(serverId)
  const [fromPlex, setFromPlex] = useState(playlist.plex_playlist_key !== null)
  return (
    <Dialog title={`Delete “${playlist.name}”?`} onClose={onClose}>
      <div className="space-y-4">
        {playlist.plex_playlist_key !== null && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={fromPlex} onChange={(e) => setFromPlex(e.target.checked)} />
            Delete the playlist in Plex too
          </label>
        )}
        {!fromPlex && playlist.plex_playlist_key !== null && (
          <p className="text-sm text-muted">It stays in Plex as an ordinary playlist and won't refresh any more.</p>
        )}
        {remove.isError && <Notice tone="error">{errorMessage(remove.error)}</Notice>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            busy={remove.isPending}
            onClick={() => remove.mutate({ id: playlist.id, fromPlex }, { onSuccess: onClose })}
          >
            Delete
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

// --- builder ---------------------------------------------------------------------------------

export function PlaylistEditorPage() {
  const { active, blocker } = useLibraryServer()
  const { id } = useParams()
  const playlistId = id && id !== 'new' ? Number(id) : null
  return (
    <>
      <PageTitle subtitle={<Link to="/playlists" className="hover:text-text">← All playlists</Link>}>
        {playlistId === null ? 'New playlist' : 'Edit playlist'}
      </PageTitle>
      {blocker ??
        (playlistId === null ? (
          <Builder serverId={active!.id} initial={null} />
        ) : (
          <LoadPlaylist serverId={active!.id} id={playlistId} />
        ))}
    </>
  )
}

function LoadPlaylist({ serverId, id }: { serverId: number; id: number }) {
  const playlist = usePlaylist(serverId, id)
  if (playlist.isPending) return <Spinner />
  if (playlist.isError) return <Notice tone="error">{errorMessage(playlist.error)}</Notice>
  return <Builder key={id} serverId={serverId} initial={playlist.data} />
}

type Draft = {
  name: string
  description: string
  schedule: Schedule
  artists: SeedArtist[]
  tracks: SeedTrack[]
  minRating: number | null
  includeUnrated: boolean
  topPercent: number
  op: Op
  limit: number
  order: Order
  genres: string[]
}

function draftFrom(p: PlaylistDetail | null): Draft {
  const d = p?.definition as Definition | undefined
  const display = (p?.display ?? {}) as { genres?: string[] }
  return {
    name: p?.name ?? '',
    description: p?.description ?? '',
    schedule: p?.schedule ?? 'manual',
    artists: p?.seed_artists ?? [],
    tracks: p?.seed_tracks ?? [],
    minRating: d?.rules?.min_rating ?? null,
    includeUnrated: d?.rules?.include_unrated ?? true,
    topPercent: d?.rules?.top_percent ?? 0,
    op: d?.rules?.op ?? 'AND',
    limit: d ? (d.limit ?? 0) : 100,
    order: d?.order ?? 'random',
    genres: display.genres ?? [],
  }
}

function definitionOf(draft: Draft): Definition | null {
  if (draft.artists.length === 0 && draft.tracks.length === 0) return null
  return {
    artists: draft.artists.map((a) => a.rating_key),
    tracks: draft.tracks.map((t) => t.rating_key),
    rules: {
      min_rating: draft.minRating,
      include_unrated: draft.includeUnrated,
      top_percent: draft.topPercent || null,
      op: draft.op,
    },
    limit: draft.limit || null,
    order: draft.order,
  }
}

function Builder({ serverId, initial }: { serverId: number; initial: PlaylistDetail | null }) {
  const navigate = useNavigate()
  const [draft, setDraft] = useState<Draft>(() => draftFrom(initial))
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }))
  const definition = useDebounced(definitionOf(draft), 350)
  const save = useSavePlaylist(serverId, initial?.id ?? null)

  const addArtists = (artists: SeedArtist[]) =>
    setDraft((d) => {
      const have = new Set(d.artists.map((a) => a.rating_key))
      return { ...d, artists: [...d.artists, ...artists.filter((a) => !have.has(a.rating_key))] }
    })
  const addTrack = (track: SeedTrack) =>
    setDraft((d) => (d.tracks.some((t) => t.rating_key === track.rating_key) ? d : { ...d, tracks: [...d.tracks, track] }))

  const submit = () => {
    const def = definitionOf(draft)
    if (!def) return
    save.mutate(
      {
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        schedule: draft.schedule,
        definition: def,
        display: { genres: draft.genres },
      },
      { onSuccess: () => navigate('/playlists') },
    )
  }
  const canSave = draft.name.trim().length > 0 && definitionOf(draft) !== null

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
      <div className="space-y-4">
        <Card title="1. Start from">
          <StartFrom serverId={serverId} onArtist={(a) => addArtists([a])} onTrack={addTrack} onGenre={(name, artists) => {
            addArtists(artists)
            setDraft((d) => (d.genres.includes(name) ? d : { ...d, genres: [...d.genres, name] }))
          }} />
          <Chosen draft={draft} serverId={serverId} set={set} />
        </Card>
        {(draft.artists.length > 0 || draft.tracks.length > 0) && (
          <Suggestions serverId={serverId} draft={draft} onArtist={(a) => addArtists([a])} onTrack={addTrack} />
        )}
        <Card title="2. Keep">
          <Rules draft={draft} set={set} />
        </Card>
        <Card title="3. Save to Plex">
          <div className="space-y-3">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Name</span>
              <input
                value={draft.name}
                maxLength={200}
                onChange={(e) => set({ name: e.target.value })}
                placeholder="e.g. Trip hop essentials"
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Description <span className="font-normal text-muted">(optional)</span></span>
              <textarea
                value={draft.description}
                maxLength={2000}
                rows={2}
                onChange={(e) => set({ description: e.target.value })}
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
              />
            </label>
            <div className="flex flex-wrap items-center gap-3">
              <Select label="Refresh" value={draft.schedule} options={SCHEDULES} onChange={(schedule) => set({ schedule })} />
              <p className="text-xs text-muted">
                A refresh picks the songs again and replaces them in the same Plex playlist.
              </p>
            </div>
            {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
            <div className="flex gap-2">
              <Button variant="primary" onClick={submit} disabled={!canSave} busy={save.isPending}>
                {initial ? 'Save and update in Plex' : 'Create in Plex'}
              </Button>
              <Link to="/playlists" className="inline-flex items-center rounded-lg px-4 py-2 text-sm font-semibold text-muted hover:bg-surface-2 hover:text-text">
                Cancel
              </Link>
            </div>
          </div>
        </Card>
      </div>
      <div className="min-w-0 lg:sticky lg:top-4 lg:self-start">
        <Preview serverId={serverId} definition={definition} />
      </div>
    </div>
  )
}

function StartFrom({
  serverId,
  onArtist,
  onTrack,
  onGenre,
}: {
  serverId: number
  onArtist: (a: SeedArtist) => void
  onTrack: (t: SeedTrack) => void
  onGenre: (name: string, artists: SeedArtist[]) => void
}) {
  const [query, setQuery] = useState('')
  const q = useDebounced(query.trim(), 250)
  const results = usePlaylistSearch(serverId, q)
  const genres = useTags(serverId, 'genre')
  const moods = useTags(serverId, 'mood')
  const [genreError, setGenreError] = useState<string | null>(null)
  const [loadingGenre, setLoadingGenre] = useState(false)

  const tagOptions = [
    { value: 0, label: 'Add a genre or mood’s top artists…' },
    ...(genres.data ?? []).map((t) => ({ value: t.id, label: t.name, group: 'Genres' })),
    ...(moods.data ?? []).map((t) => ({ value: t.id, label: t.name, group: 'Moods' })),
  ]
  const pickTag = async (id: number) => {
    if (!id) return
    const name = tagOptions.find((o) => o.value === id)?.label ?? ''
    setGenreError(null)
    setLoadingGenre(true)
    try {
      const artists = await fetchTagArtists(serverId, id)
      if (artists.length === 0) setGenreError(`No artists found for ${name}.`)
      else onGenre(name, artists)
    } catch (e) {
      setGenreError(errorMessage(e))
    } finally {
      setLoadingGenre(false)
    }
  }

  const showResults = q.length >= 2 && query.trim().length >= 2
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Escape' && setQuery('')}
          placeholder="Search artists and songs"
          aria-label="Search artists and songs"
          className="min-w-0 flex-1 basis-56 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
        />
        <Select label="Genre or mood" className="max-w-full" value={0} options={tagOptions} onChange={(id) => void pickTag(id)} />
        {loadingGenre && <Spinner small />}
      </div>
      {genreError && <Notice tone="warn">{genreError}</Notice>}
      {showResults && (
        <div className="rounded-xl border border-border bg-surface-2/50 p-2">
          {results.isPending ? (
            <Spinner small />
          ) : results.isError ? (
            <Notice tone="error">{errorMessage(results.error)}</Notice>
          ) : results.data.artists.length + results.data.tracks.length === 0 ? (
            <p className="px-2 py-1 text-sm text-muted">Nothing in your library matches “{q}”.</p>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              <ResultGroup title="Artists">
                {results.data.artists.map((a) => (
                  <ResultRow key={a.rating_key} serverId={serverId} thumb={a.thumb} title={a.title} round onAdd={() => onArtist(a)} label={`Add ${a.title}`} />
                ))}
              </ResultGroup>
              <ResultGroup title="Songs">
                {results.data.tracks.map((t) => (
                  <ResultRow
                    key={t.rating_key}
                    serverId={serverId}
                    thumb={t.thumb}
                    title={t.title}
                    subtitle={t.artist}
                    onAdd={() => onTrack(t)}
                    label={`Add ${t.title}${t.artist ? ` by ${t.artist}` : ''}`}
                  />
                ))}
              </ResultGroup>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ResultGroup({ title, children }: { title: string; children: ReactNode[] }) {
  if (children.length === 0) return null
  return (
    <div>
      <h3 className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">{title}</h3>
      <ul>{children}</ul>
    </div>
  )
}

function ResultRow({
  serverId,
  thumb,
  title,
  subtitle,
  round = false,
  onAdd,
  label,
}: {
  serverId: number
  thumb: string | null
  title: string
  subtitle?: string | null
  round?: boolean
  onAdd: () => void
  label: string
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onAdd}
        aria-label={label}
        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-surface-2"
      >
        <Artwork serverId={serverId} path={thumb} title={title} size={64} round={round} className="size-8 shrink-0 text-xs" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm">{title}</span>
          {subtitle && <span className="block truncate text-xs text-muted">{subtitle}</span>}
        </span>
        <span className="text-accent" aria-hidden>
          +
        </span>
      </button>
    </li>
  )
}

function Chip({ children, onRemove, label }: { children: ReactNode; onRemove: () => void; label: string }) {
  return (
    <li className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 py-1 pl-3 pr-1 text-sm">
      {children}
      <button
        type="button"
        onClick={onRemove}
        aria-label={label}
        className="flex size-5 items-center justify-center rounded-full text-muted hover:bg-border hover:text-text"
      >
        ×
      </button>
    </li>
  )
}

function Chosen({ draft, serverId, set }: { draft: Draft; serverId: number; set: (patch: Partial<Draft>) => void }) {
  if (draft.artists.length === 0 && draft.tracks.length === 0)
    return <p className="mt-3 text-sm text-muted">Nothing chosen yet. Artists bring all their songs; songs are always included.</p>
  return (
    <div className="mt-4 space-y-3">
      {draft.artists.length > 0 && (
        <div>
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <h3 className="text-sm font-medium">
              Artists <span className="text-muted">({draft.artists.length})</span>
            </h3>
            <button type="button" className="text-xs text-muted hover:text-text" onClick={() => set({ artists: [], genres: [] })}>
              Clear
            </button>
          </div>
          <ul className="flex flex-wrap gap-1.5">
            {draft.artists.map((a) => (
              <Chip
                key={a.rating_key}
                label={`Remove ${a.title}`}
                onRemove={() => set({ artists: draft.artists.filter((x) => x.rating_key !== a.rating_key) })}
              >
                <Artwork serverId={serverId} path={a.thumb} title={a.title} size={64} round className="-ml-2 size-5 text-[10px]" />
                {a.title}
              </Chip>
            ))}
          </ul>
          {draft.genres.length > 0 && (
            <p className="mt-1.5 text-xs text-muted">Started from {draft.genres.join(', ')}.</p>
          )}
        </div>
      )}
      {draft.tracks.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-sm font-medium">
            Songs <span className="text-muted">({draft.tracks.length}, always included)</span>
          </h3>
          <ul className="flex flex-wrap gap-1.5">
            {draft.tracks.map((t) => (
              <Chip
                key={t.rating_key}
                label={`Remove ${t.title}`}
                onRemove={() => set({ tracks: draft.tracks.filter((x) => x.rating_key !== t.rating_key) })}
              >
                {t.title}
                {t.artist && <span className="text-muted">· {t.artist}</span>}
              </Chip>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function AddChip({ children, onAdd, label }: { children: ReactNode; onAdd: () => void; label: string }) {
  return (
    <li>
      <button
        type="button"
        onClick={onAdd}
        aria-label={label}
        className="inline-flex items-center gap-1.5 rounded-full border border-dashed border-border px-3 py-1 text-sm text-muted hover:border-accent hover:text-text"
      >
        <span className="text-accent" aria-hidden>
          +
        </span>
        {children}
      </button>
    </li>
  )
}

function Suggestions({
  serverId,
  draft,
  onArtist,
  onTrack,
}: {
  serverId: number
  draft: Draft
  onArtist: (a: SeedArtist) => void
  onTrack: (t: SeedTrack) => void
}) {
  const artistKeys = draft.artists.map((a) => a.rating_key)
  const trackKeys = draft.tracks.map((t) => t.rating_key)
  const suggestions = useSuggestions(serverId, artistKeys, trackKeys)
  const chosenArtists = new Set(artistKeys)
  const chosenTracks = new Set(trackKeys)
  const data = suggestions.data
  const similar = data?.similar.filter((a) => !chosenArtists.has(a.rating_key)) ?? []
  const sonicArtists = data?.sonic_artists.filter((a) => !chosenArtists.has(a.rating_key)) ?? []
  const sonicTracks = data?.sonic_tracks.filter((t) => !chosenTracks.has(t.rating_key)) ?? []
  const empty = data && similar.length + sonicArtists.length + sonicTracks.length === 0

  return (
    <Card
      title="Add more like these"
      action={
        similar.length > 1 ? (
          <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => similar.forEach(onArtist)}>
            Add all similar
          </Button>
        ) : undefined
      }
    >
      {suggestions.isPending ? (
        <Spinner small />
      ) : suggestions.isError ? (
        <Notice tone="error">{errorMessage(suggestions.error)}</Notice>
      ) : empty ? (
        <p className="text-sm text-muted">No suggestions for these in your library.</p>
      ) : (
        <div className="space-y-3">
          {similar.length > 0 && (
            <SuggestionRow title="Similar artists">
              {similar.map((a) => (
                <AddChip key={a.rating_key} onAdd={() => onArtist(a)} label={`Add ${a.title}`}>
                  {a.title}
                </AddChip>
              ))}
            </SuggestionRow>
          )}
          {sonicArtists.length > 0 && (
            <SuggestionRow title="Sonically similar artists">
              {sonicArtists.map((a) => (
                <AddChip key={a.rating_key} onAdd={() => onArtist(a)} label={`Add ${a.title}`}>
                  {a.title}
                </AddChip>
              ))}
            </SuggestionRow>
          )}
          {sonicTracks.length > 0 && (
            <SuggestionRow title="Sonically similar songs">
              {sonicTracks.map((t) => (
                <AddChip key={t.rating_key} onAdd={() => onTrack(t)} label={`Add ${t.title}`}>
                  {t.title}
                  {t.artist && <span className="opacity-70">· {t.artist}</span>}
                </AddChip>
              ))}
            </SuggestionRow>
          )}
        </div>
      )}
    </Card>
  )
}

function SuggestionRow({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
      <ul className="flex flex-wrap gap-1.5">{children}</ul>
    </div>
  )
}

function Rules({ draft, set }: { draft: Draft; set: (patch: Partial<Draft>) => void }) {
  const both = draft.minRating !== null && draft.topPercent > 0
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="w-40 font-medium">Your rating at least</span>
        <StarRating value={draft.minRating} onChange={(minRating) => set({ minRating })} label="Minimum rating" />
        {draft.minRating === null ? (
          <span className="text-xs text-muted">Any (click a star to set)</span>
        ) : (
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.includeUnrated}
              onChange={(e) => set({ includeUnrated: e.target.checked })}
            />
            Include unrated songs
          </label>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="w-40 font-medium">Popularity</span>
        <Select
          label="Popularity"
          value={draft.topPercent}
          onChange={(topPercent) => set({ topPercent })}
          options={TOP_PERCENT.map((p) => ({
            value: p,
            label: p === 0 ? 'All songs' : `Each artist's top ${p}%`,
          }))}
        />
      </div>
      {both && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="w-40 font-medium">Combine</span>
          <Segmented
            label="Combine rules"
            value={draft.op}
            onChange={(op) => set({ op })}
            options={[
              ['AND', 'Both rules'],
              ['OR', 'Either rule'],
            ]}
          />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="w-40 font-medium">Size</span>
        <Select
          label="Size"
          value={draft.limit}
          onChange={(limit) => set({ limit })}
          options={LIMITS.map((n) => ({ value: n, label: n === 0 ? 'Every matching song' : `Up to ${n} songs` }))}
        />
        <Segmented
          label="Order"
          value={draft.order}
          onChange={(order) => set({ order })}
          options={[
            ['random', 'Shuffled'],
            ['popular', 'Most popular'],
          ]}
        />
      </div>
      <p className="text-xs text-muted">
        Songs released more than once (remasters, compilations) count once. Chosen songs are always kept.
      </p>
    </div>
  )
}

type Song = Schemas['PlaylistSong']

function Preview({ serverId, definition }: { serverId: number; definition: Definition | null }) {
  const preview = usePreview(serverId, definition)
  return (
    <Card title="Preview" action={preview.isFetching ? <Spinner small /> : undefined}>
      {definition === null ? (
        <p className="text-sm text-muted">Choose some artists or songs to see what the playlist will hold.</p>
      ) : preview.isPending ? (
        <Spinner />
      ) : preview.isError ? (
        <Notice tone="error">{errorMessage(preview.error)}</Notice>
      ) : preview.data.count === 0 ? (
        <p className="text-sm text-muted">No songs match. Loosen the rules or add more artists.</p>
      ) : (
        <div className="space-y-3">
          <div>
            <p className="font-display text-2xl font-bold">
              {formatCount(preview.data.count)} songs
            </p>
            <p className="text-sm text-muted">
              {formatListeningTime(preview.data.duration_ms)} · {formatCount(preview.data.artists)}{' '}
              {preview.data.artists === 1 ? 'artist' : 'artists'}
              {preview.data.matched > preview.data.count && ` · ${formatCount(preview.data.matched)} match`}
            </p>
          </div>
          <ol className="max-h-[60vh] space-y-1 overflow-y-auto pr-1">
            {preview.data.songs.map((s: Song) => (
              <li key={s.rating_key} className="flex items-center gap-2">
                <Artwork serverId={serverId} path={s.thumb} title={s.title} size={64} className="size-9 shrink-0 text-xs" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{s.title}</span>
                  <span className="block truncate text-xs text-muted">
                    {s.artist}
                    {s.album && ` · ${s.album}`}
                  </span>
                </span>
                <span className="text-xs tabular-nums text-muted">{formatDuration(s.duration_ms)}</span>
                <PlayButton serverId={serverId} ratingKey={s.rating_key} title={s.title} size="size-7" />
              </li>
            ))}
          </ol>
          {preview.data.count > preview.data.songs.length && (
            <p className="text-xs text-muted">
              Showing {preview.data.songs.length} of {formatCount(preview.data.count)}.
              {definition.order === 'random' && ' The order is shuffled again when the playlist is built.'}
            </p>
          )}
        </div>
      )}
    </Card>
  )
}
