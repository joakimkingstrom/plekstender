import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { errorMessage } from '../api/client'
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
import { Segmented } from '../components/Segmented'
import { Badge, Button, Card, Notice, Spinner } from '../components/ui'
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
const DEFAULT_KINDS: ReleaseKind[] = ['album', 'ep']
type View = 'new' | 'missing'

/** A release group's cover (through Plekstender, from the Cover Art Archive), or its initial. */
function Cover({ release }: { release: Release }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <div className="flex aspect-square w-full items-center justify-center rounded-lg bg-surface-2 font-display text-2xl font-bold text-muted" aria-hidden>
        {release.title.slice(0, 1)}
      </div>
    )
  }
  return (
    <img
      src={`/api/releases/${release.mbid}/cover`}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className="aspect-square w-full rounded-lg bg-surface-2 object-cover"
    />
  )
}

function ReleaseCard({ release, serverId, artist }: { release: Release; serverId: number; artist?: NewRelease['artist'] }) {
  const hide = useHideRelease(serverId)
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
    </li>
  )
}

const GRID = 'grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6'

/** Library → Releases: what's new from your artists, and what's missing from the library. */
export function ReleasesPage() {
  const { active, blocker } = useLibraryServer()
  const [params, setParams] = useSearchParams()
  const view: View = params.get('view') === 'missing' ? 'missing' : 'new'
  const chosen = (params.get('kind') ?? '').split(',').filter((k): k is ReleaseKind => KINDS.some(([key]) => key === k))
  const kinds = chosen.length ? chosen : DEFAULT_KINDS
  const [includeHidden, setIncludeHidden] = useState(false)
  const update = (patch: Record<string, string>) => setParams({ ...Object.fromEntries(params), ...patch }, { replace: true })
  const toggleKind = (kind: ReleaseKind) => {
    const next = kinds.includes(kind) ? kinds.filter((k) => k !== kind) : [...kinds, kind]
    if (next.length) update({ kind: KINDS.map(([k]) => k).filter((k) => next.includes(k)).join(',') })
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
        <label className="ml-auto flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={includeHidden} onChange={(e) => setIncludeHidden(e.target.checked)} />
          Show hidden
        </label>
      </div>
      {view === 'new' ? (
        <NewReleases serverId={serverId} kinds={kinds} includeHidden={includeHidden} />
      ) : (
        <MissingReleases serverId={serverId} kinds={kinds} includeHidden={includeHidden} />
      )}
      <p className="text-xs text-muted">
        Releases come from MusicBrainz, by the artist's MusicBrainz ID in Plex. Each artist is looked up in the background:
        the ones you follow daily, the rest every two weeks.
      </p>
    </div>
  )
}

function NewReleases({ serverId, kinds, includeHidden }: { serverId: number; kinds: ReleaseKind[]; includeHidden: boolean }) {
  const found = useNewReleases(serverId, kinds, includeHidden)
  if (found.isPending) return <Spinner />
  if (found.isError) return <Notice tone="error">{errorMessage(found.error)}</Notice>
  const { items, following, new_days } = found.data
  const upcoming = items.filter((r) => r.upcoming).reverse() // soonest first
  const out = items.filter((r) => !r.upcoming)
  if (following === 0) {
    return (
      <Card>
        <p className="text-sm text-muted">
          New releases are from the artists you follow: those you've rated 4 stars or more, and your 50 most played. Rate a
          few artists, or listen for a while, and their new releases show up here.
        </p>
      </Card>
    )
  }
  return (
    <>
      {upcoming.length > 0 && (
        <Card title="Coming up">
          <ul className={GRID}>
            {upcoming.map((r) => (
              <ReleaseCard key={r.mbid} release={r} artist={r.artist} serverId={serverId} />
            ))}
          </ul>
        </Card>
      )}
      <Card title={`Out in the last ${new_days} days`}>
        {out.length === 0 ? (
          <p className="text-sm text-muted">Nothing new from the {formatCount(following)} artists you follow.</p>
        ) : (
          <ul className={GRID}>
            {out.map((r) => (
              <ReleaseCard key={r.mbid} release={r} artist={r.artist} serverId={serverId} />
            ))}
          </ul>
        )}
        <p className="mt-4 text-xs text-muted">
          From the {formatCount(following)} artists you follow: those you've rated 4 stars or more, and your 50 most played.
        </p>
      </Card>
    </>
  )
}

function MissingReleases({ serverId, kinds, includeHidden }: { serverId: number; kinds: ReleaseKind[]; includeHidden: boolean }) {
  const [q, setQ] = useState('')
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
          {artists.map((artist) => (
            <Card
              key={artist.rating_key}
              title={
                <Link to={`/library/artists/${artist.rating_key}`} className="flex items-center gap-2 hover:underline">
                  <Artwork serverId={serverId} path={artist.thumb} title={artist.name} size={64} round className="size-7" />
                  {artist.name}
                </Link>
              }
              action={<span className="text-xs text-muted">{formatCount(artist.releases.length)} missing</span>}
            >
              <ul className={GRID}>
                {artist.releases.map((r) => (
                  <ReleaseCard key={r.mbid} release={r} serverId={serverId} />
                ))}
              </ul>
            </Card>
          ))}
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
