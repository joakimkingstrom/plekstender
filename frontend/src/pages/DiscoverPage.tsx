import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useArtistSuggestions, useHideSuggestion, type ArtistSuggestion } from '../api/discover'
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
            {found.data.items.map((artist, i) => (
              <Suggestion
                key={artist.key}
                artist={artist}
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

function Suggestion({
  artist,
  rank,
  hidden,
  busy,
  onToggle,
}: {
  artist: ArtistSuggestion
  rank: number
  hidden: boolean
  busy: boolean
  onToggle: () => void
}) {
  const more = artist.similar_to - artist.sources.length
  return (
    <li className="flex items-start gap-3 py-3 text-sm">
      <span className="w-7 shrink-0 pt-0.5 text-right text-xs tabular-nums text-muted">{rank}</span>
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold">{artist.name}</span>
          <span className="text-xs text-muted">
            similar to {formatCount(artist.similar_to)} of your artists
          </span>
          {artist.listens > 0 && (
            <span className="rounded-full bg-accent/15 px-1.5 py-px text-[11px] font-medium text-accent">
              You've played them {formatCount(artist.listens)}×
            </span>
          )}
        </p>
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
        <a
          href={`https://www.last.fm/music/${encodeURIComponent(artist.name).replace(/%20/g, '+')}`}
          target="_blank"
          rel="noreferrer"
          className="rounded-md px-2 py-1 text-xs text-muted hover:bg-surface-2 hover:text-text"
          aria-label={`${artist.name} on Last.fm`}
        >
          Last.fm
        </a>
        <Button variant="ghost" busy={busy} onClick={onToggle} className="px-2 py-1 text-xs" aria-label={`${hidden ? 'Bring back' : 'Hide'} ${artist.name}`}>
          {hidden ? 'Bring back' : 'Hide'}
        </Button>
      </div>
    </li>
  )
}
