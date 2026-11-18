import { useDeferredValue, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import {
  useActiveServer,
  useAlbum,
  useArtist,
  useArtists,
  useSyncStatus,
  useTags,
  type ArtistSort,
} from '../api/library'
import { ColumnChart } from '../components/charts'
import { Artwork, Stars, SyncPanel } from '../components/library'
import { Badge, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { formatCompact, formatCount, formatDate, formatDuration, formatRelative } from '../format'

const SORTS: { value: ArtistSort; label: string }[] = [
  { value: 'title', label: 'Name' },
  { value: 'popularity', label: 'Popularity' },
  { value: 'plays', label: 'Most played' },
  { value: 'rating', label: 'Your rating' },
  { value: 'added', label: 'Recently added' },
]

/** Resolves the active server, or explains why the library can't be shown yet. */
function useLibraryServer() {
  const { servers, active, choose } = useActiveServer()
  const sync = useSyncStatus(active?.id ?? null)
  let blocker: React.ReactNode = null
  if (servers.isPending || (active && sync.isPending)) blocker = <Spinner />
  else if (servers.isError) blocker = <Notice tone="error">{errorMessage(servers.error)}</Notice>
  else if (!active)
    blocker = (
      <Notice>
        Connect a Plex server in <Link to="/settings" className="underline">Settings</Link> first.
      </Notice>
    )
  else if (active.music_section_keys.length === 0)
    blocker = (
      <Notice>
        Pick a music library for {active.name} in <Link to="/settings" className="underline">Settings</Link>.
      </Notice>
    )
  else if (sync.data && sync.data.counts.tracks === 0)
    blocker = (
      <Card title="Setting up your library">
        <SyncPanel serverId={active.id} compact />
      </Card>
    )
  return { servers: servers.data ?? [], active, choose, blocker }
}

function ServerPicker({
  servers,
  active,
  choose,
}: {
  servers: Schemas['ServerOut'][]
  active: Schemas['ServerOut']
  choose: (id: number) => void
}) {
  if (servers.length < 2) return null
  return (
    <select
      value={active.id}
      onChange={(e) => choose(Number(e.target.value))}
      className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
      aria-label="Server"
    >
      {servers.map((s) => (
        <option key={s.id} value={s.id}>
          {s.name}
        </option>
      ))}
    </select>
  )
}

export function LibraryPage() {
  const { servers, active, choose, blocker } = useLibraryServer()
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<ArtistSort>('title')
  const [tag, setTag] = useState<number | null>(null)
  const deferredQuery = useDeferredValue(query.trim())

  return (
    <>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <PageTitle>Library</PageTitle>
        {active && <ServerPicker servers={servers} active={active} choose={choose} />}
      </div>
      {blocker ?? (
        <>
          <div className="mb-4 flex flex-wrap gap-2">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search artists"
              className="min-w-0 flex-1 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
              aria-label="Search artists"
            />
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as ArtistSort)}
              className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
              aria-label="Sort by"
            >
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <GenreChips serverId={active!.id} selected={tag} onSelect={setTag} />
          <ArtistGrid serverId={active!.id} q={deferredQuery} sort={sort} tag={tag} />
        </>
      )}
    </>
  )
}

function GenreChips({
  serverId,
  selected,
  onSelect,
}: {
  serverId: number
  selected: number | null
  onSelect: (id: number | null) => void
}) {
  const tags = useTags(serverId, 'genre')
  if (!tags.data?.length) return null
  return (
    <div className="mb-5 flex gap-2 overflow-x-auto pb-1" role="group" aria-label="Filter by genre">
      {tags.data.slice(0, 24).map((t) => {
        const on = t.id === selected
        return (
          <button
            key={t.id}
            type="button"
            aria-pressed={on}
            onClick={() => onSelect(on ? null : t.id)}
            className={`shrink-0 rounded-full border px-3 py-1 text-xs font-medium ${
              on ? 'border-accent bg-accent text-on-accent' : 'border-border text-muted hover:text-text'
            }`}
          >
            {t.name}
          </button>
        )
      })}
    </div>
  )
}

function ArtistGrid({
  serverId,
  q,
  sort,
  tag,
}: {
  serverId: number
  q: string
  sort: ArtistSort
  tag: number | null
}) {
  const artists = useArtists(serverId, q, sort, tag)
  const sentinel = useRef<HTMLDivElement>(null)
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = artists

  // Load the next page when the end of the grid scrolls into view.
  useEffect(() => {
    const el = sentinel.current
    if (!el || !hasNextPage) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !isFetchingNextPage) void fetchNextPage()
      },
      { rootMargin: '600px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  if (artists.isPending) return <Spinner />
  if (artists.isError) return <Notice tone="error">{errorMessage(artists.error)}</Notice>
  const items = artists.data.pages.flatMap((p) => p.items)
  const total = artists.data.pages[0]?.total ?? 0

  return (
    <>
      <p className="mb-3 text-xs text-muted">
        {formatCount(total)} {total === 1 ? 'artist' : 'artists'}
      </p>
      {items.length === 0 ? (
        <p className="text-sm text-muted">No artists match.</p>
      ) : (
        <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
          {items.map((a) => (
            <li key={a.rating_key}>
              <Link to={`/library/artists/${a.rating_key}`} className="group block">
                <Artwork serverId={serverId} path={a.thumb} title={a.title} round className="w-full transition-opacity group-hover:opacity-85" />
                <div className="mt-2 truncate text-center text-sm font-medium">{a.title}</div>
                <div className="flex justify-center">
                  {a.user_rating !== null ? (
                    <Stars rating={a.user_rating} />
                  ) : (
                    <span className="text-xs text-muted">
                      {a.album_count} {a.album_count === 1 ? 'album' : 'albums'}
                    </span>
                  )}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <div ref={sentinel} className="flex justify-center py-6">
        {isFetchingNextPage && <Spinner small />}
      </div>
    </>
  )
}

function TrackList({
  tracks,
  showAlbum = false,
  showListens = false,
}: {
  tracks: Schemas['TrackOut'][]
  showAlbum?: boolean
  /** A column with the user's own listens of each song. */
  showListens?: boolean
}) {
  return (
    <ol className="divide-y divide-border">
      {tracks.map((t, i) => (
        <li key={t.rating_key} className="flex items-center gap-3 py-2 text-sm">
          <span className="w-6 shrink-0 text-right text-xs text-muted tabular-nums">
            {showAlbum ? i + 1 : (t.number ?? '')}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate">{t.title}</span>
            {(showAlbum || t.original_title) && (
              <span className="block truncate text-xs text-muted">
                {[t.original_title, showAlbum ? t.album_title : null].filter(Boolean).join(' · ')}
              </span>
            )}
          </span>
          <span className="hidden w-14 text-right text-xs text-muted tabular-nums sm:block" title="Last.fm listeners">
            {formatCompact(t.popularity)}
          </span>
          {showListens && (
            <span className="w-10 text-right text-xs text-muted tabular-nums" title="Your listens">
              {t.listens ? `${formatCount(t.listens)}×` : ''}
            </span>
          )}
          <Stars rating={t.user_rating} className="w-20 justify-end" />
          <span className="w-10 text-right text-xs text-muted tabular-nums">{formatDuration(t.duration_ms)}</span>
        </li>
      ))}
    </ol>
  )
}

function TagBadges({ tags }: { tags: Schemas['TagOut'][] }) {
  if (!tags.length) return null
  const order = ['genre', 'style', 'mood', 'country']
  const sorted = [...tags].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind))
  return (
    <div className="flex flex-wrap gap-1.5">
      {sorted.map((t) => (
        <Badge key={t.id} tone={t.kind === 'genre' ? 'accent' : 'muted'}>
          {t.name}
        </Badge>
      ))}
    </div>
  )
}

function Summary({ text }: { text: string | null }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  return (
    <div className="text-sm text-muted">
      <p className={open ? 'whitespace-pre-line' : 'line-clamp-4 whitespace-pre-line'}>{text}</p>
      {text.length > 300 && (
        <button type="button" className="mt-1 text-accent" onClick={() => setOpen(!open)}>
          {open ? 'Less' : 'More'}
        </button>
      )}
    </div>
  )
}

function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className="mb-4 inline-block text-sm text-muted hover:text-text">
      ← {label}
    </Link>
  )
}

function YourListens({ listens, showSongs }: { listens: Schemas['ItemListensOut']; showSongs: boolean }) {
  if (!listens.listens) {
    return (
      <Card title="Your listens">
        <p className="text-sm text-muted">
          None yet. Your listening history includes plays from Plex and imported Last.fm scrobbles.
        </p>
      </Card>
    )
  }
  const unit = (n: number) => `${formatCount(n)} ${n === 1 ? 'listen' : 'listens'}`
  return (
    <Card title="Your listens">
      <div className="space-y-4">
        <div>
          <p className="font-display text-3xl font-bold">{formatCount(listens.listens)}</p>
          <p className="text-xs text-muted">
            {listens.first_at && `First ${formatDate(listens.first_at)}`}
            {listens.last_at && ` · last ${formatRelative(listens.last_at)}`}
          </p>
        </div>
        {listens.years.length > 1 && (
          <ColumnChart
            height={120}
            caption="Your listens per year"
            unit={unit}
            points={listens.years.map((y) => ({ label: String(y.year), axis: String(y.year), value: y.listens }))}
          />
        )}
        {showSongs && (
          <ol className="space-y-1.5">
            {listens.songs.slice(0, 5).map((song, i) => (
              <li key={song.name} className="flex items-center gap-3 text-sm">
                <span className="w-4 text-right text-xs text-muted tabular-nums">{i + 1}</span>
                <span className="min-w-0 flex-1 truncate">{song.name}</span>
                <span className="text-xs text-muted tabular-nums">{formatCount(song.listens)}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </Card>
  )
}

export function ArtistPage() {
  const { blocker, active } = useLibraryServer()
  const key = Number(useParams().key)
  if (blocker) return blocker
  return <ArtistView serverId={active!.id} artistKey={key} />
}

function ArtistView({ serverId, artistKey }: { serverId: number; artistKey: number }) {
  const artist = useArtist(serverId, artistKey)
  if (artist.isPending) return <Spinner />
  if (artist.isError) return <Notice tone="error">{errorMessage(artist.error)}</Notice>
  const a = artist.data

  return (
    <>
      <BackLink to="/library" label="Library" />
      <div className="mb-8 flex flex-col gap-6 sm:flex-row">
        <Artwork serverId={serverId} path={a.thumb} title={a.title} size={400} round className="w-40 shrink-0 self-center sm:self-start" />
        <div className="min-w-0 flex-1 space-y-3">
          <h1 className="text-3xl font-bold tracking-tight">{a.title}</h1>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
            <Stars rating={a.user_rating} />
            <span>{formatCount(a.plays)} plays</span>
            {a.popularity !== null && <span title="Sum of top-song Last.fm listeners">Popularity {formatCompact(a.popularity)}</span>}
          </div>
          <TagBadges tags={a.tags} />
          <Summary text={a.summary} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
        <div className="space-y-6">
          {a.top_tracks.length > 0 && (
            <Card title="Most popular songs">
              <TrackList tracks={a.top_tracks} showAlbum showListens={a.your_listens.listens > 0} />
            </Card>
          )}
          <Card title="Albums">
            <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              {a.albums.map((al) => (
                <li key={al.rating_key}>
                  <Link to={`/library/albums/${al.rating_key}`} className="group block">
                    <Artwork serverId={serverId} path={al.thumb} title={al.title} className="w-full group-hover:opacity-85" />
                    <div className="mt-2 truncate text-sm font-medium">{al.title}</div>
                    <div className="flex items-center justify-between gap-2 text-xs text-muted">
                      <span>{al.year ?? ''}</span>
                      {al.user_rating !== null && <Stars rating={al.user_rating} />}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <div className="space-y-6">
        <YourListens listens={a.your_listens} showSongs />
        {a.similar.length > 0 && (
          <Card title="Similar artists">
            <ul className="flex flex-wrap gap-2">
              {a.similar.map((s) =>
                s.rating_key ? (
                  <li key={s.name}>
                    <Link to={`/library/artists/${s.rating_key}`} className="block rounded-full bg-accent/15 px-3 py-1 text-sm text-accent hover:bg-accent/25">
                      {s.name}
                    </Link>
                  </li>
                ) : (
                  <li key={s.name} className="rounded-full bg-surface-2 px-3 py-1 text-sm text-muted" title="Not in your library">
                    {s.name}
                  </li>
                ),
              )}
            </ul>
          </Card>
        )}
        </div>
      </div>
    </>
  )
}

export function AlbumPage() {
  const { blocker, active } = useLibraryServer()
  const key = Number(useParams().key)
  if (blocker) return blocker
  return <AlbumView serverId={active!.id} albumKey={key} />
}

function AlbumView({ serverId, albumKey }: { serverId: number; albumKey: number }) {
  const album = useAlbum(serverId, albumKey)
  if (album.isPending) return <Spinner />
  if (album.isError) return <Notice tone="error">{errorMessage(album.error)}</Notice>
  const al = album.data
  const discs = new Set(al.tracks.map((t) => t.disc ?? 1))

  return (
    <>
      {al.artist_rating_key ? (
        <BackLink to={`/library/artists/${al.artist_rating_key}`} label={al.artist_title ?? 'Artist'} />
      ) : (
        <BackLink to="/library" label="Library" />
      )}
      <div className="mb-8 flex flex-col gap-6 sm:flex-row">
        <Artwork serverId={serverId} path={al.thumb} title={al.title} size={600} className="w-56 shrink-0 self-center sm:self-start" />
        <div className="min-w-0 flex-1 space-y-3">
          <h1 className="text-3xl font-bold tracking-tight">{al.title}</h1>
          <p className="text-sm text-muted">
            {[al.artist_title, al.year, al.studio].filter(Boolean).join(' · ')}
          </p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
            <Stars rating={al.user_rating} />
            {al.critic_rating !== null && <span>Critics {al.critic_rating.toFixed(1)}/10</span>}
            <span>{formatCount(al.plays)} plays</span>
          </div>
          <TagBadges tags={al.tags} />
          <Summary text={al.summary} />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
        <Card>
          {[...discs].sort((a, b) => a - b).map((disc) => (
            <div key={disc}>
              {discs.size > 1 && <h3 className="mb-1 mt-3 text-xs font-semibold uppercase tracking-wider text-muted">Disc {disc}</h3>}
              <TrackList
                tracks={al.tracks.filter((t) => (t.disc ?? 1) === disc)}
                showListens={al.your_listens.listens > 0}
              />
            </div>
          ))}
        </Card>
        <div>
          <YourListens listens={al.your_listens} showSongs={false} />
        </div>
      </div>
    </>
  )
}
