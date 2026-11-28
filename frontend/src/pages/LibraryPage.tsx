import { useArtistPastGigs } from '../api/gigs'
import { useArtistGigs, useArtistsWithGigs } from '../api/live'
import { GigMarker } from '../components/GigMarker'
import { GigRow } from '../components/GigRow'
import { SongSources } from '../components/SongSources'
import { ArtistCovers, CoverOf } from '../components/Covers'
import { SongVersions } from '../components/SongVersions'
import { VideoCover } from '../components/VideoCover'
import { AlbumCoverView } from '../components/AlbumCoverView'
import { AlbumLyricsIcon, SongLyricsIcon } from '../components/LyricsIcon'
import { useLyricsIcons } from '../api/hooks'
import { useDeferredValue, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Zoomable } from '../components/Lightbox'
import { ListensDialog } from '../components/listens'
import { Select } from '../components/Select'
import { Tooltip } from '../components/Tooltip'
import { Link, useParams, useSearchParams } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import {
  imageUrl,
  useAlbum,
  useArtist,
  useArtistNeighbours,
  useArtists,
  useLibraryAlbums,
  useTags,
  type AlbumSort,
  type ArtistSort,
  type TagKind,
} from '../api/library'
import { ColumnChart } from '../components/charts'
import { Artwork } from '../components/library'
import { RateStars } from '../components/RateStars'
import { useLibraryServer } from '../components/useLibraryServer'
import { AlbumFiles, ArtistFiles } from '../components/MusicFiles'
import { ArtistNfo } from '../components/NfoCards'
import { TagHistory } from '../components/TagEditor'
import { TagChips } from '../components/TagChip'
import { Segmented } from '../components/Segmented'
import { Card, Notice, Spinner } from '../components/ui'
import { formatCompact, formatCount, formatDate, formatDuration, formatRelative } from '../format'
import { countryFlag, countryName } from '../places'
import { COVER_PIXELS, useCoverSize, type CoverSize } from '../coverSize'
import { CoverSizeSwitch } from '../components/CoverSizeSwitch'

const SORTS: { value: ArtistSort; label: string }[] = [
  { value: 'title', label: 'Name' },
  { value: 'popularity', label: 'Popularity' },
  { value: 'plays', label: 'Most played' },
  { value: 'rating', label: 'Your rating' },
  { value: 'added', label: 'Recently added' },
]

export function ServerPicker({
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
    <Select label="Server" value={active.id} onChange={choose} options={servers.map((s) => ({ value: s.id, label: s.name }))} />
  )
}

type View = 'artists' | 'albums'
const VIEWS: [View, string][] = [
  ['artists', 'Artists'],
  ['albums', 'Albums'],
]

/** Library: its artists, or its albums (?view=albums), each searchable and filterable. */
export function LibraryPage() {
  const { active, blocker } = useLibraryServer()
  const [params, setParams] = useSearchParams()
  const view: View = params.get('view') === 'albums' ? 'albums' : 'artists'
  const viewSwitch = (
    <Segmented
      label="Show"
      value={view}
      options={VIEWS}
      onChange={(v) => setParams(v === 'albums' ? { view: v } : {}, { replace: true })}
    />
  )

  return (
    <>
      {blocker ?? (
        <>
          {view === 'albums' ? (
            <AlbumsView serverId={active!.id} viewSwitch={viewSwitch} />
          ) : (
            <ArtistsView serverId={active!.id} viewSwitch={viewSwitch} />
          )}
        </>
      )}
    </>
  )
}

/** The first row: the search on the left, the view (artists or albums) on the right. (The tag
 * kind, the chips, the cover size, and the sort are the row below.) */
function Toolbar({ search, right }: { search: ReactNode; right: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <div className="min-w-48 flex-1">{search}</div>
      <div className="ml-auto flex shrink-0 gap-2">{right}</div>
    </div>
  )
}

/** Genres, styles, or moods: which the chips under the search row are. */
function TagKindSwitch({ value, onChange }: { value: TagKind; onChange: (kind: TagKind) => void }) {
  return <Segmented label="Filter by" value={value} options={FILTER_KINDS} onChange={onChange} />
}

function ArtistsView({ serverId, viewSwitch }: { serverId: number; viewSwitch: ReactNode }) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<ArtistSort>('title')
  const [tag, setTag] = useState<number | null>(null)
  const [kind, setKind] = useState<TagKind>('genre')
  const [size, setSize] = useCoverSize('library-cover-size')
  const deferredQuery = useDeferredValue(query.trim())
  return (
    <>
      <Toolbar
        search={
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search artists"
            className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
            aria-label="Search artists"
          />
        }
        right={viewSwitch}
      />
      <TagFilter
        serverId={serverId}
        kind={kind}
        selected={tag}
        onSelect={setTag}
        start={
          <TagKindSwitch
            value={kind}
            onChange={(k) => {
              setKind(k)
              setTag(null)
            }}
          />
        }
        end={
          <>
            <CoverSizeSwitch value={size} onChange={setSize} />
            <Select label="Sort by" value={sort} onChange={setSort} options={SORTS} />
          </>
        }
      />
      <ArtistGrid serverId={serverId} q={deferredQuery} sort={sort} tag={tag} size={size} />
    </>
  )
}

const ALBUM_SORTS: { value: AlbumSort; label: string }[] = [
  { value: 'artist', label: 'Artist' },
  { value: 'title', label: 'Title' },
  { value: 'newest', label: 'Newest' },
  { value: 'oldest', label: 'Oldest' },
  { value: 'added', label: 'Recently added' },
  { value: 'rating', label: 'Your rating' },
  { value: 'plays', label: 'Most played' },
  { value: 'popularity', label: 'Popularity' },
]

/** Release types as the type filter names them (others as Plex has them). */
const TYPE_NAMES: Record<string, string> = {
  Album: 'Albums',
  EP: 'EPs',
  Single: 'Singles',
  Compilation: 'Compilations',
  Live: 'Live',
  Soundtrack: 'Soundtracks',
  Remix: 'Remixes',
  Demo: 'Demos',
  Mixtape: 'Mixtapes',
}

function AlbumsView({ serverId, viewSwitch }: { serverId: number; viewSwitch: ReactNode }) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<AlbumSort>('artist')
  const [tag, setTag] = useState<number | null>(null)
  const [kind, setKind] = useState<TagKind>('genre')
  const [type, setType] = useState<string | null>(null)
  const [size, setSize] = useCoverSize('library-album-size')
  const q = useDeferredValue(query.trim())
  const albums = useLibraryAlbums(serverId, q, sort, tag, type)
  const counts = albums.data?.pages[0]?.types ?? {}
  const types = Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  const all = types.reduce((n, [, c]) => n + c, 0)
  return (
    <>
      <Toolbar
        search={
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search albums or artists"
            className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
            aria-label="Search albums or artists"
          />
        }
        right={viewSwitch}
      />
      <TagFilter
        serverId={serverId}
        kind={kind}
        selected={tag}
        onSelect={setTag}
        className="mb-3"
        start={
          <TagKindSwitch
            value={kind}
            onChange={(k) => {
              setKind(k)
              setTag(null)
            }}
          />
        }
        end={
          <>
            <CoverSizeSwitch value={size} onChange={setSize} />
            <Select label="Sort by" value={sort} onChange={setSort} options={ALBUM_SORTS} />
          </>
        }
      />
      <div className="mb-5 flex items-center gap-3">
        <div className="no-scrollbar flex min-w-0 flex-1 gap-2 overflow-x-auto py-1" role="group" aria-label="Release type">
          {([[null, 'All', all], ...types.map(([t, n]) => [t, TYPE_NAMES[t] ?? t, n])] as [string | null, string, number][]).map(
            ([value, label, n]) => {
              const on = type === value
              return (
                <button
                  key={value ?? 'all'}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setType(value)}
                  className={`shrink-0 rounded-full border px-3 py-1 text-xs font-medium ${
                    on ? 'border-accent bg-accent text-on-accent' : 'border-border text-muted hover:text-text'
                  }`}
                >
                  {label} <span className="tabular-nums opacity-75">{formatCount(n)}</span>
                </button>
              )
            },
          )}
        </div>
      </div>
      <AlbumGrid serverId={serverId} albums={albums} size={size} />
    </>
  )
}

function AlbumGrid({
  serverId,
  albums,
  size,
}: {
  serverId: number
  albums: ReturnType<typeof useLibraryAlbums>
  size: CoverSize
}) {
  const sentinel = useRef<HTMLDivElement>(null)
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = albums
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

  if (albums.isPending) return <Spinner />
  if (albums.isError) return <Notice tone="error">{errorMessage(albums.error)}</Notice>
  const items = albums.data.pages.flatMap((p) => p.items)
  const total = albums.data.pages[0]?.total ?? 0
  return (
    <>
      <p className="mb-3 text-xs text-muted">
        {formatCount(total)} {total === 1 ? 'album' : 'albums'}
      </p>
      {items.length === 0 ? (
        <p className="text-sm text-muted">No albums match.</p>
      ) : (
        <ul className={ARTIST_GRIDS[size]} aria-label="Albums">
          {items.map((al) => (
            <li key={al.rating_key}>
              <Link to={`/library/albums/${al.rating_key}`} className="group block">
                <VideoCover video={al.video}>
                  <Artwork serverId={serverId} path={al.thumb} title={al.title} size={COVER_PIXELS[size]} className="w-full transition-opacity group-hover:opacity-85" />
                </VideoCover>
                <div className="mt-2 truncate text-sm font-medium">{al.title}</div>
              </Link>
              <div className="truncate text-xs text-muted">
                {al.artist_rating_key ? (
                  <Link to={`/library/artists/${al.artist_rating_key}`} className="hover:text-text hover:underline">
                    {al.artist_title}
                  </Link>
                ) : (
                  al.artist_title
                )}
                {[al.year, al.release_type !== 'Album' ? al.release_type : null].filter(Boolean).map((p) => ` · ${p}`).join('')}
              </div>
              <RateStars serverId={serverId} ratingKey={al.rating_key} value={al.user_rating} label={`Rate ${al.title}`} size="size-3.5" />
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

const FILTER_KINDS: [TagKind, string][] = [
  ['genre', 'Genres'],
  ['style', 'Styles'],
  ['mood', 'Moods'],
]

/** The second row: genres, styles, or moods (`start`, the switch for `kind`), the ones to
 * filter by as chips, and the cover size and sort at the right end (`end`). */
function TagFilter({
  serverId,
  kind,
  selected,
  onSelect,
  start,
  end,
  className = 'mb-5',
}: {
  serverId: number
  kind: TagKind
  selected: number | null
  onSelect: (id: number | null) => void
  /** At the start of the row (the kind of tag). */
  start?: ReactNode
  /** At the right end of the row (the cover size and the sort). */
  end?: ReactNode
  className?: string
}) {
  const tags = useTags(serverId, kind)
  const label = FILTER_KINDS.find(([k]) => k === kind)?.[1] ?? ''
  const chosen = tags.data?.find((t) => t.id === selected)
  return (
    // On phones the chips get a line of their own, under the switch and the sort.
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-2 sm:flex-nowrap ${className}`}>
      {start && <div className="shrink-0">{start}</div>}
      <div
        className="no-scrollbar order-last flex min-w-0 basis-full gap-2 overflow-x-auto py-1 sm:order-none sm:flex-1 sm:basis-auto"
        role="group"
        aria-label={`Filter by ${label.toLowerCase()}`}
      >
        {tags.data?.length === 0 && <span className="py-1 text-xs text-muted">No {label.toLowerCase()} yet.</span>}
        {tags.data?.slice(0, 40).map((t) => {
          const on = t.id === selected
          return (
            <button
              key={t.id}
              type="button"
              aria-pressed={on}
              onClick={() => onSelect(on ? null : t.id)}
              className={`shrink-0 rounded-full border px-3 py-1 text-xs font-medium ${
                on ? 'border-accent bg-accent text-on-accent' : 'border-border text-muted hover:text-text'
              } ${kind === 'mood' ? 'italic' : ''}`}
            >
              {t.name}
            </button>
          )
        })}
      </div>
      {chosen && (
        <Link to={`/library/tags/${chosen.id}`} className="shrink-0 py-1 text-xs text-accent hover:underline">
          {chosen.name} page →
        </Link>
      )}
      {end && <div className="ml-auto flex shrink-0 gap-2">{end}</div>}
    </div>
  )
}

/** The library's artists, by cover size. */
const ARTIST_GRIDS: Record<CoverSize, string> = {
  small: 'grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8',
  medium: 'grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6',
  large: 'grid grid-cols-1 gap-5 min-[420px]:grid-cols-2 md:grid-cols-3 lg:grid-cols-4',
}
/** An artist's albums (in the wider column), by cover size. */
const ALBUM_GRIDS: Record<CoverSize, string> = {
  small: 'grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-5',
  medium: 'grid grid-cols-2 gap-4 sm:grid-cols-3',
  large: 'grid grid-cols-1 gap-5 min-[420px]:grid-cols-2',
}

function ArtistGrid({
  serverId,
  q,
  sort,
  tag,
  size,
}: {
  serverId: number
  q: string
  sort: ArtistSort
  tag: number | null
  size: CoverSize
}) {
  const artists = useArtists(serverId, q, sort, tag)
  const withGigs = useArtistsWithGigs(serverId)
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
        <ul className={ARTIST_GRIDS[size]}>
          {items.map((a) => (
            <li key={a.rating_key}>
              <Link to={`/library/artists/${a.rating_key}`} className="group block">
                <div className="relative">
                  <Artwork
                    serverId={serverId}
                    path={a.thumb}
                    title={a.title}
                    size={COVER_PIXELS[size]}
                    round
                    className="w-full transition-opacity group-hover:opacity-85"
                  />
                  {withGigs.data?.has(a.rating_key) && <GigMarker className="bottom-[6%] right-[6%] size-7" />}
                </div>
                <div className="mt-2 truncate text-center text-sm font-medium">{a.title}</div>
              </Link>
              <div className="flex justify-center">
                <RateStars serverId={serverId} ratingKey={a.rating_key} value={a.user_rating} label={`Rate ${a.title}`} size="size-3.5" />
              </div>
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
  serverId,
  tracks,
  showAlbum = false,
  showListens = false,
}: {
  serverId: number
  tracks: Schemas['TrackOut'][]
  showAlbum?: boolean
  /** A column with the user's own listens of each song. */
  showListens?: boolean
}) {
  const lyricsIcons = useLyricsIcons()
  const [versionsOf, setVersionsOf] = useState<Schemas['TrackOut'] | null>(null)
  return (
    <>
    {versionsOf && (
      <SongVersions serverId={serverId} ratingKey={versionsOf.rating_key} title={versionsOf.title} onClose={() => setVersionsOf(null)} />
    )}
    <ol className="divide-y divide-border">
      {tracks.map((t, i) => (
        <li key={t.rating_key} className="flex items-center gap-3 py-2 text-sm">
          <span className="w-6 shrink-0 text-right text-xs text-muted tabular-nums">
            {showAlbum ? i + 1 : (t.number ?? '')}
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5">
              <button type="button" onClick={() => setVersionsOf(t)} className="truncate text-left hover:underline" title="Every version of this song">
                {t.title}
              </button>
              {t.seen_live > 0 && <SeenLive times={t.seen_live} />}
              {lyricsIcons && <SongLyricsIcon kind={t.lyrics} />}
            </span>
            {(showAlbum || t.original_title) && (
              <span className="block truncate text-xs text-muted">
                {[t.original_title, showAlbum ? t.album_title : null].filter(Boolean).join(' · ')}
              </span>
            )}
            {t.cover_of && <CoverOf serverId={serverId} trackKey={t.rating_key} cover={t.cover_of} />}
          </span>
          <Tooltip content="Last.fm listeners" className="hidden w-14 justify-end sm:inline-flex">
            <span className="text-xs text-muted tabular-nums">{formatCompact(t.popularity)}</span>
          </Tooltip>
          {showListens && (
            <Tooltip content="Your listens of this song" className="w-10 justify-end">
              <span className="text-xs text-muted tabular-nums">{t.listens ? `${formatCount(t.listens)}×` : ''}</span>
            </Tooltip>
          )}
          <RateStars serverId={serverId} ratingKey={t.rating_key} value={t.user_rating} label={`Rate ${t.title}`} size="size-4" />
          <span className="w-10 text-right text-xs text-muted tabular-nums">{formatDuration(t.duration_ms)}</span>
        </li>
      ))}
    </ol>
    </>
  )
}

/** A song you've heard played at a gig you went to. */
function SeenLive({ times }: { times: number }) {
  const what = times === 1 ? 'a gig you went to' : `${formatCount(times)} gigs you went to`
  return (
    <Tooltip content={`Played at ${what}`} className="shrink-0">
      <span className="rounded-full bg-accent/15 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-accent">
        Seen live{times > 1 ? ` ${formatCount(times)}×` : ''}
        <span className="sr-only">: played at {what}</span>
      </span>
    </Tooltip>
  )
}

const folded = (name: string) => name.toLowerCase().replace(/^the\s+/, '').trim()

/** The gigs you've been to where this artist played, each with what they played. */
function PastGigs({ serverId, artistKey, name }: { serverId: number; artistKey: number; name: string }) {
  const gigs = useArtistPastGigs(serverId, artistKey)
  const [open, setOpen] = useState<number | null>(null)
  if (!gigs.data?.length) return null
  return (
    <Card
      title="Gigs you've been to"
      action={
        <Link to="/gigs" className="text-sm text-accent hover:underline">
          All gigs
        </Link>
      }
    >
      <ul className="divide-y divide-border">
        {gigs.data.map((g) => {
          const setlist = g.setlists.find((s) => folded(s.artist) === folded(name))
          const others = g.artists.filter((a) => folded(a.name) !== folded(name)).map((a) => a.name)
          return (
            <li key={g.id} className="py-2.5 text-sm">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                <span className="font-semibold">{[g.name, g.venue].filter(Boolean).join(' · ') || 'A gig'}</span>
                <span className="text-xs text-muted tabular-nums">{formatDate(g.date)}</span>
              </div>
              <p className="text-xs text-muted">
                {[g.city, others.length ? `with ${others.slice(0, 4).join(', ')}${others.length > 4 ? '…' : ''}` : null]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              {setlist && setlist.songs.length > 0 && (
                <>
                  <button
                    type="button"
                    aria-expanded={open === g.id}
                    onClick={() => setOpen(open === g.id ? null : g.id)}
                    className="mt-1 text-xs text-accent hover:underline"
                  >
                    {open === g.id ? 'Hide the setlist' : `Setlist (${setlist.songs.length} ${setlist.songs.length === 1 ? 'song' : 'songs'})`}
                  </button>
                  {open === g.id && (
                    <ol className="mt-1 list-decimal space-y-0.5 pl-6 text-xs">
                      {setlist.songs.map((song, i) => (
                        <li key={i} className={song.tape ? 'text-muted' : ''}>
                          {song.name}
                          {song.cover && <span className="text-muted"> ({song.cover} cover)</span>}
                          {song.tape && <span className="text-muted"> (tape)</span>}
                        </li>
                      ))}
                    </ol>
                  )}
                  {open === g.id && setlist.url && (
                    <a href={setlist.url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-xs text-muted underline">
                      On setlist.fm
                    </a>
                  )}
                </>
              )}
            </li>
          )
        })}
      </ul>
    </Card>
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

function YourListens({
  listens,
  showSongs,
  name,
}: {
  listens: Schemas['ItemListensOut']
  showSongs: boolean
  name: string
}) {
  const [year, setYear] = useState<number | null>(null)
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
            onSelect={(i) => setYear(listens.years[i].year)}
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
      {year !== null && (
        <ListensDialog
          title={`${name} in ${year}`}
          filter={{
            artist: listens.artist_keys,
            album: listens.album_key,
            start: new Date(year, 0, 1).toISOString(),
            end: new Date(year + 1, 0, 1).toISOString(),
          }}
          onClose={() => setYear(null)}
        />
      )}
    </Card>
  )
}

const RELEASE_ORDER = ['Album', 'EP', 'Single', 'Live', 'Compilation', 'Soundtrack']
const RELEASE_HEADINGS: Record<string, string> = {
  Album: 'Albums',
  EP: 'EPs',
  Single: 'Singles',
  Live: 'Live',
  Compilation: 'Compilations',
  Soundtrack: 'Soundtracks',
}

/** An artist's releases grouped by type (albums, EPs, singles, live, compilations…), each
 * group in the order given (oldest first), as the artist page shows them. */
function releaseGroups(albums: Schemas['AlbumSummary'][]): [string, Schemas['AlbumSummary'][]][] {
  const groups = new Map<string, Schemas['AlbumSummary'][]>()
  for (const al of albums) groups.set(al.release_type ?? 'Album', [...(groups.get(al.release_type ?? 'Album') ?? []), al])
  const rank = (type: string) => {
    const i = RELEASE_ORDER.indexOf(type)
    return i < 0 ? RELEASE_ORDER.length : i
  }
  return [...groups.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
}

/** An artist's releases, grouped by type (albums, EPs, singles, live, compilations…). */
function Releases({ serverId, albums }: { serverId: number; albums: Schemas['AlbumSummary'][] }) {
  const [size, setSize] = useCoverSize('artist-cover-size')
  const lyricsIcons = useLyricsIcons()
  const ordered = releaseGroups(albums)
  return (
    <>
      {ordered.map(([type, items]) => (
        <Card
          key={type}
          title={ordered.length > 1 ? (RELEASE_HEADINGS[type] ?? type) : 'Albums'}
          action={type === ordered[0][0] ? <CoverSizeSwitch value={size} onChange={setSize} /> : undefined}
        >
          <ul className={ALBUM_GRIDS[size]}>
            {items.map((al) => (
                <li key={al.rating_key}>
                  <Link to={`/library/albums/${al.rating_key}`} className="group block">
                    <VideoCover video={al.video}>
                      <Artwork serverId={serverId} path={al.thumb} title={al.title} size={COVER_PIXELS[size]} className="w-full group-hover:opacity-85" />
                    </VideoCover>
                    <div className="mt-2 truncate text-sm font-medium">{al.title}</div>
                  </Link>
                  <div className="flex items-center justify-between gap-2 text-xs text-muted">
                    <span className="flex items-center gap-1.5">
                      {al.year ?? ''}
                      {lyricsIcons && <AlbumLyricsIcon lyrics={al.lyrics} />}
                    </span>
                    <RateStars serverId={serverId} ratingKey={al.rating_key} value={al.user_rating} label={`Rate ${al.title}`} size="size-3.5" />
                  </div>
                </li>
              ))}
          </ul>
        </Card>
      ))}
    </>
  )
}

export function ArtistPage() {
  const { blocker, active } = useLibraryServer()
  const key = Number(useParams().key)
  if (blocker) return blocker
  return (
    <>
      <ArtistView serverId={active!.id} artistKey={key} />
      {/* Edits to this artist's files, with undo (server owners). */}
      {active!.is_owner && (
        <div className="mt-4 space-y-4">
          <ArtistFiles serverId={active!.id} artistKey={key} />
          <ArtistNfo serverId={active!.id} artistKey={key} />
          <TagHistory serverId={active!.id} artist={key} />
        </div>
      )}
    </>
  )
}

type Step = { rating_key: number; title: string }

/** Previous and next, as subtle arrows in the margins either side of the page, halfway down
 * the window: placed from where the page's content really starts and ends (measured, so
 * nothing wide can end up under them), and left out when the margins are too narrow. */
function PageSteps({
  what,
  path,
  previous,
  next,
}: {
  /** "artist" or "album": for the labels. */
  what: string
  /** Where a step goes, e.g. "/library/artists". */
  path: string
  previous: Step | null | undefined
  next: Step | null | undefined
}) {
  const margins = usePageMargins()
  const fits = margins !== null && margins.left >= ARROW_ROOM && margins.right >= ARROW_ROOM
  const step = (to: Step | null | undefined, side: 'previous' | 'next') =>
    to && fits ? (
      <Link
        to={`${path}/${to.rating_key}`}
        aria-label={`${side === 'previous' ? 'Previous' : 'Next'} ${what}: ${to.title}`}
        title={to.title}
        style={side === 'previous' ? { left: margins.left - ARROW_ROOM + ARROW_GAP } : { right: margins.right - ARROW_ROOM + ARROW_GAP }}
        className="fixed top-1/2 z-20 flex -translate-y-1/2 items-center justify-center rounded-full p-2 text-muted/60 transition-colors hover:bg-surface-2 hover:text-text"
      >
        <svg viewBox="0 0 24 24" className="size-8" aria-hidden>
          <path d={side === 'previous' ? 'M15 4l-8 8 8 8' : 'M9 4l8 8-8 8'} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </Link>
    ) : null
  return (
    <nav aria-label={`Other ${what}s`}>
      {step(previous, 'previous')}
      {step(next, 'next')}
    </nav>
  )
}

/** Previous and next artist, by name as the library lists them. */
function ArtistSteps({ serverId, artistKey }: { serverId: number; artistKey: number }) {
  const found = useArtistNeighbours(serverId, artistKey)
  return <PageSteps what="artist" path="/library/artists" previous={found.data?.previous} next={found.data?.next} />
}

/** Previous and next of the artist's releases, in the order their page shows them: by type
 * (albums, EPs, singles…), each oldest first. */
function AlbumSteps({ serverId, albumKey, artistKey }: { serverId: number; albumKey: number; artistKey: number }) {
  const artist = useArtist(serverId, artistKey)
  const order = releaseGroups(artist.data?.albums ?? []).flatMap(([, items]) => items)
  const i = order.findIndex((al) => al.rating_key === albumKey)
  if (i < 0) return null
  return <PageSteps what="album" path="/library/albums" previous={order[i - 1]} next={order[i + 1]} />
}

const ARROW_GAP = 8 // px between an arrow and the content, and the window's edge
const ARROW_ROOM = 48 + ARROW_GAP * 2 // an arrow (p-2 around 32 px) with its gaps

/** How much room there is left and right of the page's content (<main>, without its
 * padding, and however wide its content really is), kept up to date as the window and the
 * page change size. Null before it's measured. */
function usePageMargins(): { left: number; right: number } | null {
  const [margins, setMargins] = useState<{ left: number; right: number } | null>(null)
  useLayoutEffect(() => {
    const main = document.querySelector('main')
    if (!main) return
    const measure = () => {
      const box = main.getBoundingClientRect()
      const style = getComputedStyle(main)
      const left = box.left + parseFloat(style.paddingLeft || '0')
      const right = box.left + Math.max(box.width, main.scrollWidth) - parseFloat(style.paddingRight || '0')
      const next = { left: Math.round(left), right: Math.round(window.innerWidth - right) }
      setMargins((m) => (m && m.left === next.left && m.right === next.right ? m : next))
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(main)
    window.addEventListener('resize', measure)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [])
  return margins
}

function ArtistView({ serverId, artistKey }: { serverId: number; artistKey: number }) {
  const artist = useArtist(serverId, artistKey)
  const gigs = useArtistGigs(serverId, artistKey)
  if (artist.isPending) return <Spinner />
  if (artist.isError) return <Notice tone="error">{errorMessage(artist.error)}</Notice>
  const a = artist.data

  return (
    <>
      <BackLink to="/library" label="Library" />
      <ArtistSteps serverId={serverId} artistKey={artistKey} />
      <div className="mb-8 flex flex-col gap-6 sm:flex-row">
        <div className="relative w-40 shrink-0 self-center sm:self-start">
          <Zoomable image={a.thumb ? { src: imageUrl(serverId, a.thumb, 1000)!, alt: a.title } : null} className="w-full">
            <Artwork serverId={serverId} path={a.thumb} title={a.title} size={400} round className="w-full" />
          </Zoomable>
          {(gigs.data?.length ?? 0) > 0 && <GigMarker className="bottom-[6%] right-[6%] size-9" />}
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex items-start justify-between gap-3">
            <h1 className="min-w-0 text-3xl font-bold tracking-tight">{a.title}</h1>
            {a.country && countryFlag(a.country) && (
              <Tooltip content={countryName(a.country)}>
                <span role="img" aria-label={`From ${countryName(a.country)}`} className="shrink-0 text-3xl leading-tight">
                  {countryFlag(a.country)}
                </span>
              </Tooltip>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
            <RateStars serverId={serverId} ratingKey={a.rating_key} value={a.user_rating} label={`Rate the artist ${a.title}`} size="size-5" />
            <Tooltip content="Plex plays or listens in your history, whichever is higher"><span>{formatCount(a.plays)} plays</span></Tooltip>
            {a.popularity !== null && <Tooltip content="Sum of the Last.fm listeners of the artist's top songs"><span>Popularity {formatCompact(a.popularity)}</span></Tooltip>}
          </div>
          <TagChips tags={a.tags} />
          <Summary text={a.summary} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          {gigs.data && gigs.data.length > 0 && (
            <Card
              title="Upcoming gigs"
              action={
                <Link to="/gigs" className="text-sm text-accent hover:underline">
                  All gigs
                </Link>
              }
            >
              <ul className="-mx-3 divide-y divide-border">
                {gigs.data.map((show) => (
                  <GigRow key={show.id} show={show} serverId={serverId} />
                ))}
              </ul>
            </Card>
          )}
          <PastGigs serverId={serverId} artistKey={artistKey} name={a.title} />
          {a.top_tracks.length > 0 && (
            <Card title="Most popular songs">
              <TrackList serverId={serverId} tracks={a.top_tracks} showAlbum showListens={a.your_listens.listens > 0} />
            </Card>
          )}
          <Releases serverId={serverId} albums={a.albums} />
        </div>
        <div className="space-y-6">
        <YourListens listens={a.your_listens} showSongs name={a.title} />
        <ArtistCovers serverId={serverId} artistKey={a.rating_key} />
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
                  <li key={s.name}>
                    <Tooltip content="Not in your library">
                      <span className="block rounded-full bg-surface-2 px-3 py-1 text-sm text-muted">{s.name}</span>
                    </Tooltip>
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
  return (
    <>
      <AlbumView serverId={active!.id} albumKey={key} />
      {active!.is_owner && (
        <div className="mt-4">
          <AlbumFiles serverId={active!.id} albumKey={key} />
        </div>
      )}
    </>
  )
}

function AlbumView({ serverId, albumKey }: { serverId: number; albumKey: number }) {
  const album = useAlbum(serverId, albumKey)
  const lyricsIcons = useLyricsIcons()
  if (album.isPending) return <Spinner />
  if (album.isError) return <Notice tone="error">{errorMessage(album.error)}</Notice>
  const al = album.data
  const discs = new Set(al.tracks.map((t) => t.disc ?? 1))

  return (
    <>
      {al.artist_rating_key ? (
        <>
          <BackLink to={`/library/artists/${al.artist_rating_key}`} label={al.artist_title ?? 'Artist'} />
          <AlbumSteps serverId={serverId} albumKey={al.rating_key} artistKey={al.artist_rating_key} />
        </>
      ) : (
        <BackLink to="/library" label="Library" />
      )}
      <div className="mb-8 flex flex-col gap-6 sm:flex-row">
        <AlbumCoverView serverId={serverId} album={al} className="w-56 shrink-0 self-center sm:self-start">
          <VideoCover video={al.video}>
            <Artwork serverId={serverId} path={al.thumb} title={al.title} size={600} className="w-full" />
          </VideoCover>
        </AlbumCoverView>
        <div className="min-w-0 flex-1 space-y-3">
          <h1 className="text-3xl font-bold tracking-tight">{al.title}</h1>
          <p className="text-sm text-muted">
            {[al.artist_title, al.year, al.studio].filter(Boolean).join(' · ')}
          </p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
            <RateStars serverId={serverId} ratingKey={al.rating_key} value={al.user_rating} label={`Rate the album ${al.title}`} size="size-5" />
            {al.critic_rating !== null && <span>Critics {al.critic_rating.toFixed(1)}/10</span>}
            <Tooltip content="Plex plays or listens in your history, whichever is higher"><span>{formatCount(al.plays)} plays</span></Tooltip>
            {lyricsIcons && <AlbumLyricsIcon lyrics={al.lyrics} />}
          </div>
          <TagChips tags={al.tags} />
          <Summary text={al.summary} />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          {[...discs].sort((a, b) => a - b).map((disc) => (
            <div key={disc}>
              {discs.size > 1 && <h3 className="mb-1 mt-3 text-xs font-semibold uppercase tracking-wider text-muted">Disc {disc}</h3>}
              <TrackList
                serverId={serverId}
                tracks={al.tracks.filter((t) => (t.disc ?? 1) === disc)}
                showListens={al.your_listens.listens > 0}
              />
            </div>
          ))}
        </Card>
        <div className="space-y-6">
          <YourListens listens={al.your_listens} showSongs={false} name={al.title} />
          {al.song_sources && (
            <SongSources
              serverId={serverId}
              sources={al.song_sources}
              unmatched={al.sources_unmatched ?? 0}
              albumArtist={al.artist_title}
              compilation={al.release_type !== 'Live'}
            />
          )}
        </div>
      </div>
    </>
  )
}
