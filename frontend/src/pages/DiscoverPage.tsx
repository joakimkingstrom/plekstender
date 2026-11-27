import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useArtistSuggestions, useHideSuggestion, type ArtistSuggestion } from '../api/discover'
import { useLidarrAvailable } from '../api/lidarr'
import { HideToggle } from '../components/HideToggle'
import { LidarrArtistDialog } from '../components/LidarrArtist'
import { LidarrAddIcon } from '../components/LidarrDownload'
import { Tooltip } from '../components/Tooltip'
import { Segmented } from '../components/Segmented'
import { useLibraryServer } from '../components/useLibraryServer'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { formatCount } from '../format'

const PAGE = 50

/** Library → Discover: artists to add to the library. */
export function DiscoverPage() {
  const { active, blocker } = useLibraryServer()
  if (blocker) return blocker
  return <Discover serverId={active!.id} />
}

function Discover({ serverId }: { serverId: number }) {
  const [view, setView] = useState<'suggested' | 'hidden'>('suggested')
  const [limit, setLimit] = useState(PAGE)
  const found = useArtistSuggestions(serverId, view === 'hidden', limit)
  const hide = useHideSuggestion(serverId)
  const hiddenCount = found.data?.hidden ?? 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted">
          Artists you don't have yet that Plex says are similar to the ones you do. The more of your artists they're like,
          the higher they come; your own listens to them (scrobbles count too) break ties.
        </p>
        <Segmented
          label="Show"
          value={view}
          options={[
            ['suggested', 'Artists to add'],
            ['hidden', `Hidden${hiddenCount ? ` (${formatCount(hiddenCount)})` : ''}`],
          ]}
          onChange={(v) => {
            setView(v)
            setLimit(PAGE)
          }}
        />
      </div>
      {hide.isError && <Notice tone="error">{errorMessage(hide.error)}</Notice>}
      {found.isPending ? (
        <Spinner />
      ) : found.isError ? (
        <Notice tone="error">{errorMessage(found.error)}</Notice>
      ) : found.data.items.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">
            {view === 'hidden'
              ? "You haven't hidden any."
              : 'Nothing to suggest: either your library already has every artist Plex calls similar, or the next library sync will bring them.'}
          </p>
        </Card>
      ) : (
        <Card>
          <ol className={`-my-2 divide-y divide-border ${found.isPlaceholderData ? 'opacity-70' : ''}`}>
            {found.data.items.map((artist, i, all) => (
              <Suggestion
                key={artist.key}
                artist={artist}
                mostPlayed={Math.max(...all.map((a) => a.listens))}
                rank={i + 1}
                hidden={view === 'hidden'}
                busy={hide.isPending && hide.variables?.artist.key === artist.key}
                onToggle={() => hide.mutate({ artist, hide: view !== 'hidden' })}
              />
            ))}
          </ol>
          {found.data.total > found.data.items.length && (
            <div className="mt-3 flex items-center justify-between text-sm text-muted">
              <span>
                {formatCount(found.data.items.length)} of {formatCount(found.data.total)}
              </span>
              <Button variant="ghost" busy={found.isFetching} onClick={() => setLimit(limit + PAGE)}>
                Show more
              </Button>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

/** How much you've played an artist (though not in the library): a bar on a log scale
 * against the most played suggestion, so 3 plays still shows next to 300. */
function PlaysBar({ plays, most, name }: { plays: number; most: number; name: string }) {
  const share = most > 0 ? Math.log1p(plays) / Math.log1p(most) : 0
  return (
    <div className="flex items-center gap-2 pt-0.5" title={`You've played ${name} ${formatCount(plays)} ${plays === 1 ? 'time' : 'times'}`}>
      <div
        className="h-1.5 w-24 overflow-hidden rounded-full bg-surface-2"
        role="meter"
        aria-label={`Your plays of ${name}`}
        aria-valuemin={0}
        aria-valuemax={most}
        aria-valuenow={plays}
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(6, share * 100)}%` }} />
      </div>
      <span className="text-[11px] font-medium tabular-nums text-accent">
        {formatCount(plays)} {plays === 1 ? 'play' : 'plays'}
      </span>
    </div>
  )
}

function Suggestion({
  artist,
  mostPlayed,
  rank,
  hidden,
  busy,
  onToggle,
}: {
  artist: ArtistSuggestion
  /** The most plays of any suggestion shown: the scale for the plays bar. */
  mostPlayed: number
  rank: number
  hidden: boolean
  busy: boolean
  onToggle: () => void
}) {
  const more = artist.similar_to - artist.sources.length
  const lidarr = useLidarrAvailable()
  const [adding, setAdding] = useState(false)
  return (
    <li className="flex items-start gap-3 py-3 text-sm">
      <span className="w-7 shrink-0 pt-0.5 text-right text-xs tabular-nums text-muted">{rank}</span>
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold">{artist.name}</span>
          <span className="text-xs text-muted">
            similar to {formatCount(artist.similar_to)} of your artists
          </span>
        </p>
        {artist.listens > 0 && <PlaysBar plays={artist.listens} most={mostPlayed} name={artist.name} />}
        <p className="text-xs text-muted">
          {artist.sources.map((s, i) => (
            <span key={s.rating_key}>
              {i > 0 && (i === artist.sources.length - 1 && more <= 0 ? ' and ' : ', ')}
              <Link to={`/library/artists/${s.rating_key}`} className="text-text hover:underline">
                {s.name}
              </Link>
            </span>
          ))}
          {more > 0 && ` and ${formatCount(more)} more`}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Tooltip content="On Last.fm">
          <a
            href={`https://www.last.fm/music/${encodeURIComponent(artist.name).replace(/%20/g, '+')}`}
            target="_blank"
            rel="noreferrer"
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-text"
            aria-label={`${artist.name} on Last.fm`}
          >
            <svg viewBox="0 0 16 16" className="size-4" aria-hidden>
              <path d="M9.5 2.5h4v4M13.5 2.5L7.5 8.5M12 9.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </a>
        </Tooltip>
        {lidarr && (
          <Tooltip content="Add to Lidarr">
            <Button variant="ghost" size="xs" onClick={() => setAdding(true)} aria-label={`Add ${artist.name} to Lidarr`}>
              <LidarrAddIcon />
            </Button>
          </Tooltip>
        )}
        <HideToggle hidden={hidden} name={artist.name} busy={busy} onToggle={onToggle} />
      </div>
      {adding && <LidarrArtistDialog name={artist.name} onClose={() => setAdding(false)} />}
    </li>
  )
}
