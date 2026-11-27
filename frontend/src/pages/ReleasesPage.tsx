import { createContext, useContext, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useLidarrAvailable, useLidarrStates, type LidarrAlbumState } from '../api/lidarr'
import { useMe, useSetPreferences } from '../api/hooks'
import {
  formatReleaseDate,
  useHideRelease,
  useMissingReleases,
  useNewReleases,
  type NewRelease,
  type Release,
  type ReleaseKind,
} from '../api/releases'
import { Artwork } from '../components/library'
import { DownloadIcon, LidarrBadge, LidarrDownloadDialog, LidarrMonitorButton } from '../components/LidarrDownload'
import { Segmented } from '../components/Segmented'
import { Select } from '../components/Select'
import { Badge, Button, Card, Notice, Spinner } from '../components/ui'
import { Tooltip } from '../components/Tooltip'
import { useLibraryServer } from '../components/useLibraryServer'
import { formatCount } from '../format'

const KINDS: [ReleaseKind, string][] = [
  ['album', 'Albums'],
  ['ep', 'EPs'],
  ['single', 'Singles'],
  ['live', 'Live'],
  ['compilation', 'Compilations'],
  ['other', 'Other'],
]
const KIND_NAME: Record<ReleaseKind, string> = {
  album: 'Album',
  ep: 'EP',
  single: 'Single',
  live: 'Live',
  compilation: 'Compilation',
  other: 'Other',
}
type View = 'new' | 'missing'
type Settings = Required<Schemas['ReleaseSettings']>
const DEFAULT_SETTINGS: Settings = { kinds: ['album', 'ep'], follow_rating: 8, follow_top: 50 }

const RATINGS = [
  { value: 0, label: 'any rating (off)' },
  ...[4, 5, 6, 7, 8, 9, 10].map((r) => ({ value: r, label: `${Math.floor(r / 2)}${r % 2 ? '½' : ''} stars` })),
]
const TOPS = [0, 25, 50, 100, 200, 500, 1000].map((n) => ({ value: n, label: n === 0 ? 'none' : String(n) }))

/** Your Releases settings (saved on your account), and a way to change some of them. */
function useReleaseSettings(): [Settings, (patch: Partial<Settings>) => void] {
  const me = useMe()
  const save = useSetPreferences()
  const settings = { ...DEFAULT_SETTINGS, ...me.data?.preferences?.releases }
  return [settings, (patch) => save.mutate({ releases: { ...settings, ...patch } })]
}

/** A release group's cover (through Plekstender, from the Cover Art Archive), or its initial. */
function Cover({ release }: { release: Release }) {
  const [failed, setFailed] = useState(false)
  const large = useContext(CoverSizeContext) === 'large'

  if (failed) {
    return (
      <div className="flex aspect-square w-full items-center justify-center rounded-lg bg-surface-2 font-display text-2xl font-bold text-muted" aria-hidden>
        {release.title.slice(0, 1)}
      </div>
    )
  }
  return (
    <img
      src={`/api/releases/${release.mbid}/cover${large ? '?size=500' : ''}`}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className="aspect-square w-full rounded-lg bg-surface-2 object-cover"
    />
  )
}

/** Each album's state in Lidarr (when it's available), for the cards in a grid. */
const LidarrStates = createContext<Record<string, LidarrAlbumState> | null>(null)

function ReleaseCard({ release, serverId, artist }: { release: Release; serverId: number; artist?: NewRelease['artist'] }) {
  const hide = useHideRelease(serverId)
  const lidarr = useContext(LidarrStates)
  const state = lidarr?.[release.mbid]
  const out = !('upcoming' in release && release.upcoming)
  const [downloading, setDownloading] = useState(false)
  return (
    <li className={`min-w-0 ${release.hidden ? 'opacity-50' : ''}`}>
      <a
        href={`https://musicbrainz.org/release-group/${release.mbid}`}
        target="_blank"
        rel="noreferrer"
        className="group block"
        title="Open on MusicBrainz"
      >
        <Cover release={release} />
        <p className="mt-1.5 truncate text-sm font-medium group-hover:underline">{release.title}</p>
      </a>
      {artist && (
        <Link to={`/library/artists/${artist.rating_key}`} className="block truncate text-xs hover:underline">
          {artist.name}
        </Link>
      )}
      <p className="truncate text-xs text-muted">
        {KIND_NAME[release.kind]} · {formatReleaseDate(release.first_release_date)}
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        {release.in_library && <Badge tone="ok">In your library</Badge>}
        <LidarrBadge state={state} upcoming={!out} />
        {lidarr && !out && (state === 'absent' || state === 'unmonitored') && <LidarrMonitorButton mbid={release.mbid} title={release.title} />}
        {lidarr && out && !release.in_library && state !== 'downloaded' && (
          <Tooltip content="Download with Lidarr">
            <Button variant="ghost" size="xs" onClick={() => setDownloading(true)} aria-label={`Download ${release.title} with Lidarr`}>
              <DownloadIcon />
            </Button>
          </Tooltip>
        )}
        <Button
          variant="ghost"
          size="xs"
          busy={hide.isPending}
          onClick={() => hide.mutate({ mbid: release.mbid, hide: !release.hidden })}
          aria-label={`${release.hidden ? 'Show' : 'Hide'} ${release.title}`}
        >
          {release.hidden ? 'Show again' : 'Hide'}
        </Button>
      </div>
      {downloading && (
        <LidarrDownloadDialog mbid={release.mbid} title={release.title} artist={artist?.name} onClose={() => setDownloading(false)} />
      )}
    </li>
  )
}

type CoverSize = 'small' | 'medium' | 'large'
const SIZES: [CoverSize, string][] = [
  ['small', 'S'],
  ['medium', 'M'],
  ['large', 'L'],
]
const GRIDS: Record<CoverSize, string> = {
  small: 'grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8',
  medium: 'grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6',
  large: 'grid grid-cols-1 gap-5 min-[420px]:grid-cols-2 md:grid-cols-3 lg:grid-cols-4',
}
const SIZE_KEY = 'releases-cover-size'
const CoverSizeContext = createContext<CoverSize>('medium')

/** The covers' size, remembered in this browser. */
function useCoverSize(): [CoverSize, (size: CoverSize) => void] {
  const [size, setSize] = useState<CoverSize>(() => {
    try {
      const stored = localStorage.getItem(SIZE_KEY)
      return SIZES.some(([s]) => s === stored) ? (stored as CoverSize) : 'medium'
    } catch {
      return 'medium'
    }
  })
  const change = (next: CoverSize) => {
    setSize(next)
    try {
      localStorage.setItem(SIZE_KEY, next)
    } catch {
      // private browsing: just for now
    }
  }
  return [size, change]
}

const OUT_FILTER_KEY = 'releases-out-filter'

/** Out lately: all, or only what's not in the library (remembered in this browser). */
function useOutFilter(): ['all' | 'missing', (show: 'all' | 'missing') => void] {
  const [show, setShow] = useState<'all' | 'missing'>(() => {
    try {
      return localStorage.getItem(OUT_FILTER_KEY) === 'missing' ? 'missing' : 'all'
    } catch {
      return 'all'
    }
  })
  const change = (next: 'all' | 'missing') => {
    setShow(next)
    try {
      localStorage.setItem(OUT_FILTER_KEY, next)
    } catch {
      // private browsing: just for now
    }
  }
  return [show, change]
}

function useGrid(): string {
  return GRIDS[useContext(CoverSizeContext)]
}

/** Library → Releases: what's new from your artists, and what's missing from the library. */
export function ReleasesPage() {
  const { active, blocker } = useLibraryServer()
  const [params, setParams] = useSearchParams()
  const view: View = params.get('view') === 'missing' ? 'missing' : 'new'
  const [settings, saveSettings] = useReleaseSettings()
  const kinds = settings.kinds.length ? settings.kinds : DEFAULT_SETTINGS.kinds
  const [includeHidden, setIncludeHidden] = useState(false)
  const [size, setSize] = useCoverSize()
  const update = (patch: Record<string, string>) => setParams({ ...Object.fromEntries(params), ...patch }, { replace: true })
  // Saved on your account: the same kinds next time, on any device.
  const toggleKind = (kind: ReleaseKind) => {
    const next = kinds.includes(kind) ? kinds.filter((k) => k !== kind) : [...kinds, kind]
    if (next.length) saveSettings({ kinds: KINDS.map(([k]) => k).filter((k) => next.includes(k)) })
  }

  if (blocker) return blocker
  const serverId = active!.id
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          label="Releases"
          value={view}
          onChange={(v) => update({ view: v })}
          options={[
            ['new', 'New releases'],
            ['missing', 'Missing from your library'],
          ]}
        />
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Kinds of release">
          {KINDS.map(([kind, label]) => (
            <button
              key={kind}
              type="button"
              aria-pressed={kinds.includes(kind)}
              onClick={() => toggleKind(kind)}
              className={`rounded-full px-3 py-1 text-xs font-medium ${kinds.includes(kind) ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted hover:text-text'}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={includeHidden} onChange={(e) => setIncludeHidden(e.target.checked)} />
            Show hidden
          </label>
          <Segmented label="Cover size" value={size} onChange={setSize} options={SIZES} />
        </div>
      </div>
      <CoverSizeContext value={size}>
      {view === 'new' ? (
        <NewReleases
          serverId={serverId}
          kinds={kinds}
          includeHidden={includeHidden}
          settings={settings}
          onSettings={saveSettings}
        />
      ) : (
        <MissingReleases serverId={serverId} kinds={kinds} includeHidden={includeHidden} />
      )}
      </CoverSizeContext>
      <p className="text-xs text-muted">
        Releases come from MusicBrainz, by the artist's MusicBrainz ID in Plex. Each artist is looked up in the background:
        the ones you follow daily, the rest every two weeks.
      </p>
    </div>
  )
}

function Following({ settings, onSettings }: { settings: Settings; onSettings: (patch: Partial<Settings>) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted">Follow artists you've rated</span>
      <Select
        size="sm"
        label="Follow artists rated at least"
        value={settings.follow_rating}
        options={RATINGS}
        onChange={(follow_rating) => onSettings({ follow_rating })}
      />
      <span className="text-muted">or more, and your</span>
      <Select
        size="sm"
        label="Follow your most played artists"
        value={settings.follow_top}
        options={TOPS}
        onChange={(follow_top) => onSettings({ follow_top })}
      />
      <span className="text-muted">most played</span>
    </div>
  )
}

function NewReleases({
  serverId,
  kinds,
  includeHidden,
  settings,
  onSettings,
}: {
  serverId: number
  kinds: ReleaseKind[]
  includeHidden: boolean
  settings: Settings
  onSettings: (patch: Partial<Settings>) => void
}) {
  const found = useNewReleases(serverId, kinds, includeHidden, settings)
  const grid = useGrid()
  const [show, setShow] = useOutFilter()
  const lidarrOn = useLidarrAvailable()
  const lidarr = useLidarrStates((found.data?.items ?? []).map((r) => r.mbid), lidarrOn)
  const followingControls = <Following settings={settings} onSettings={onSettings} />
  if (found.isPending) return <Spinner />
  if (found.isError) return <Notice tone="error">{errorMessage(found.error)}</Notice>
  const { items, following, following_with_mbid: withMbid, following_read: read, new_days } = found.data
  const followingRow = followingControls
  const reading = read < withMbid
  const upcoming = items.filter((r) => r.upcoming).reverse() // soonest first
  const out = items.filter((r) => !r.upcoming)
  const shown = show === 'missing' ? out.filter((r) => !r.in_library) : out
  if (following === 0) {
    return (
      <Card>
        {followingRow}
        <p className="mt-3 text-sm text-muted">
          You don't follow any artists yet. Rate a few artists, listen for a while, or follow more of them above, and their
          new releases show up here.
        </p>
      </Card>
    )
  }
  return (
    <>
      {followingRow}
      {reading && (
        <Notice>
          Still reading releases from MusicBrainz: {formatCount(read)} of the {formatCount(withMbid)} artists you follow so
          far (a few hundred an hour). Their new releases show up here as they're read.
        </Notice>
      )}
      {upcoming.length > 0 && (
        <Card title="Coming up">
          <LidarrStates value={lidarrOn ? (lidarr.data ?? {}) : null}>
            <ul className={grid}>
              {upcoming.map((r) => (
                <ReleaseCard key={r.mbid} release={r} artist={r.artist} serverId={serverId} />
              ))}
            </ul>
          </LidarrStates>
        </Card>
      )}
      <Card
        title={`Out in the last ${new_days} days`}
        action={
          <Segmented
            label="Which albums"
            value={show}
            onChange={setShow}
            options={[
              ['all', 'All'],
              ['missing', 'Not in your library'],
            ]}
          />
        }
      >
        {out.length > 0 && shown.length === 0 ? (
          <p className="text-sm text-muted">You have everything that's out lately.</p>
        ) : out.length === 0 ? (
          <p className="text-sm text-muted">
            {read === 0
              ? 'Nothing yet: none of the artists you follow have had their releases read.'
              : reading
                ? `Nothing in the last ${new_days} days from the ${formatCount(read)} artists read so far.`
                : `Nothing in the last ${new_days} days from the ${formatCount(following)} artists you follow.`}
          </p>
        ) : (
          <LidarrStates value={lidarrOn ? (lidarr.data ?? {}) : null}>
            <ul className={grid}>
              {shown.map((r) => (
                <ReleaseCard key={r.mbid} release={r} artist={r.artist} serverId={serverId} />
              ))}
            </ul>
          </LidarrStates>
        )}
        <p className="mt-4 text-xs text-muted">
          Everything the {formatCount(following)} artists you follow have put out (of the kinds chosen above), including what
          you already have: that's marked "In your library".
          {following > withMbid &&
            ` ${formatCount(following - withMbid)} of them have no MusicBrainz ID in Plex, so their releases can't be looked up.`}
        </p>
      </Card>
    </>
  )
}

const KIND_PLURAL: Record<ReleaseKind, [string, string]> = {
  album: ['album', 'albums'],
  ep: ['EP', 'EPs'],
  single: ['single', 'singles'],
  live: ['live album', 'live albums'],
  compilation: ['compilation', 'compilations'],
  other: ['other', 'other'],
}

/** "11 albums, 2 EPs", in the order kinds are listed. */
function kindsText(releases: Release[]): string {
  return KINDS.map(([kind]) => [kind, releases.filter((r) => r.kind === kind).length] as const)
    .filter(([, n]) => n > 0)
    .map(([kind, n]) => `${formatCount(n)} ${KIND_PLURAL[kind][n === 1 ? 0 : 1]}`)
    .join(', ')
}

type MissingArtist = { rating_key: number; name: string; thumb: string | null; releases: Release[] }

/** An artist and the releases the library doesn't have: one line until opened. */
function MissingArtistCard({
  serverId,
  artist,
  open,
  onToggle,
}: {
  serverId: number
  artist: MissingArtist
  open: boolean
  onToggle: () => void
}) {
  const panel = `missing-${artist.rating_key}`
  const grid = useGrid()
  const lidarrOn = useLidarrAvailable()
  const lidarr = useLidarrStates(open ? artist.releases.map((r) => r.mbid) : [], lidarrOn)
  return (
    <section className="rounded-2xl border border-border bg-surface">
      <div className="flex items-center gap-3 p-4 sm:px-5">
        <Link to={`/library/artists/${artist.rating_key}`} className="flex min-w-0 items-center gap-2 font-semibold hover:underline">
          <Artwork serverId={serverId} path={artist.thumb} title={artist.name} size={64} round className="size-8 shrink-0" />
          <span className="truncate">{artist.name}</span>
        </Link>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panel}
          aria-label={`${open ? 'Hide' : 'Show'} the ${formatCount(artist.releases.length)} missing from ${artist.name}`}
          onClick={onToggle}
          className="ml-auto flex min-w-0 items-center gap-2 rounded-lg px-2 py-1 text-right text-xs text-muted hover:bg-surface-2 hover:text-text"
        >
          <span className="truncate">
            {formatCount(artist.releases.length)} missing
            <span className="hidden sm:inline"> · {kindsText(artist.releases)}</span>
          </span>
          <svg
            viewBox="0 0 20 20"
            className={`size-4 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden
          >
            <path d="M5 8l5 5 5-5" />
          </svg>
        </button>
      </div>
      {open && (
        <LidarrStates value={lidarrOn ? (lidarr.data ?? {}) : null}>
          <ul id={panel} className={`${grid} px-4 pb-4 sm:px-5 sm:pb-5`}>
            {artist.releases.map((r) => (
              <ReleaseCard key={r.mbid} release={r} serverId={serverId} />
            ))}
          </ul>
        </LidarrStates>
      )}
    </section>
  )
}

function MissingReleases({ serverId, kinds, includeHidden }: { serverId: number; kinds: ReleaseKind[]; includeHidden: boolean }) {
  const [q, setQ] = useState('')
  // Artists opened (all start closed).
  const [opened, setOpened] = useState<Set<number>>(() => new Set())
  const toggle = (key: number) =>
    setOpened((was) => {
      const next = new Set(was)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  const found = useMissingReleases(serverId, kinds, q.trim(), includeHidden)
  const first = found.data?.pages[0]
  const artists = found.data?.pages.flatMap((p) => p.artists) ?? []
  const coverage = first?.coverage
  return (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Find an artist"
          aria-label="Find an artist"
          className="w-full max-w-xs rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
        />
        {first && (
          <p className="text-sm text-muted">
            {formatCount(first.total_artists)} {first.total_artists === 1 ? 'artist has' : 'artists have'} releases you don't
          </p>
        )}
        {artists.length > 0 && (
          <div className="ml-auto flex gap-1">
            <Button variant="ghost" size="sm" onClick={() => setOpened(new Set(artists.map((a) => a.rating_key)))}>
              Expand all
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setOpened(new Set())}>
              Collapse all
            </Button>
          </div>
        )}
      </div>
      {coverage && coverage.checked < coverage.with_mbid && (
        <Notice>
          Still reading releases from MusicBrainz: {formatCount(coverage.checked)} of {formatCount(coverage.with_mbid)}{' '}
          artists so far. A few hundred are read an hour.
        </Notice>
      )}
      {found.isPending ? (
        <Spinner />
      ) : found.isError ? (
        <Notice tone="error">{errorMessage(found.error)}</Notice>
      ) : artists.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">
            {q.trim() ? 'No artist by that name is missing anything.' : 'Nothing missing: you have everything they released.'}
          </p>
        </Card>
      ) : (
        <>
          <div className="space-y-2">
            {artists.map((artist) => (
              <MissingArtistCard
                key={artist.rating_key}
                serverId={serverId}
                artist={artist}
                open={opened.has(artist.rating_key)}
                onToggle={() => toggle(artist.rating_key)}
              />
            ))}
          </div>
          {found.hasNextPage && (
            <div className="flex justify-center">
              <Button busy={found.isFetchingNextPage} onClick={() => void found.fetchNextPage()}>
                More artists
              </Button>
            </div>
          )}
        </>
      )}
      {coverage && coverage.with_mbid < coverage.artists && (
        <p className="text-xs text-muted">
          {formatCount(coverage.artists - coverage.with_mbid)} of your {formatCount(coverage.artists)} artists have no
          MusicBrainz ID in Plex, so their releases can't be looked up. Plex's music agent adds one when it matches an
          artist.
        </p>
      )}
    </>
  )
}
