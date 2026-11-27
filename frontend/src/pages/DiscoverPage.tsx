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
const TOP = 3 // big cards
const BANNERS = 10 // rows with the artist's banner, after the cards

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
  const mostPlayed = Math.max(0, ...(found.data?.items ?? []).map((a) => a.listens))
  const actions = (artist: ArtistSuggestion) => ({
    hidden: view === 'hidden',
    busy: hide.isPending && hide.variables?.artist.key === artist.key,
    onToggle: () => hide.mutate({ artist, hide: view !== 'hidden' }),
  })

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
        <div className={`space-y-4 ${found.isPlaceholderData ? 'opacity-70' : ''}`}>
          {/* Suggested: the top 3 as big cards (their fanart.tv background under their logo),
              4–10 with their banner, the rest a list; hidden ones: just the list. */}
          {view === 'suggested' && (
            <ol className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Top suggestions">
              {found.data.items.slice(0, TOP).map((artist, i) => (
                <TopCard key={artist.key} artist={artist} rank={i + 1} mostPlayed={mostPlayed} {...actions(artist)} />
              ))}
            </ol>
          )}
        <Card>
          <ol className="-my-2 divide-y divide-border" start={view === 'suggested' ? TOP + 1 : 1}>
            {found.data.items.slice(view === 'suggested' ? TOP : 0).map((artist, i) => {
              const rank = i + 1 + (view === 'suggested' ? TOP : 0)
              return view === 'suggested' && rank <= BANNERS ? (
                <BannerRow key={artist.key} artist={artist} rank={rank} mostPlayed={mostPlayed} {...actions(artist)} />
              ) : (
                <Suggestion key={artist.key} artist={artist} mostPlayed={mostPlayed} rank={rank} {...actions(artist)} />
              )
            })}
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
        </div>
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

type SuggestionProps = {
  artist: ArtistSuggestion
  /** The most plays of any suggestion shown: the scale for the plays bar. */
  mostPlayed: number
  rank: number
  hidden: boolean
  busy: boolean
  onToggle: () => void
}

/** "Radiohead, Portishead and 3 more": the library artists it's like, each a link. */
function Sources({ artist, linkClass = 'text-text' }: { artist: ArtistSuggestion; linkClass?: string }) {
  const more = artist.similar_to - artist.sources.length
  return (
    <>
      {artist.sources.map((s, i) => (
        <span key={s.rating_key}>
          {i > 0 && (i === artist.sources.length - 1 && more <= 0 ? ' and ' : ', ')}
          <Link to={`/library/artists/${s.rating_key}`} className={`${linkClass} hover:underline`}>
            {s.name}
          </Link>
        </span>
      ))}
      {more > 0 && ` and ${formatCount(more)} more`}
    </>
  )
}

/** Last.fm, add to Lidarr, hide: `onArt`, round and dark, to sit on a picture. */
function Actions({ artist, hidden, busy, onToggle, onArt = false }: Pick<SuggestionProps, 'artist' | 'hidden' | 'busy' | 'onToggle'> & { onArt?: boolean }) {
  const lidarr = useLidarrAvailable()
  const [adding, setAdding] = useState(false)
  return (
    <div
      className={`flex shrink-0 items-center gap-1 ${
        onArt ? 'text-[#f1efe9] [&_a]:rounded-full [&_a]:bg-black/55 [&_a]:text-[#f1efe9] [&_button]:rounded-full [&_button]:bg-black/55 [&_button]:text-[#f1efe9]' : ''
      }`}
    >
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
      {adding && <LidarrArtistDialog name={artist.name} onClose={() => setAdding(false)} />}
    </div>
  )
}

/** 1–3: a big card, the artist's fanart.tv background darkened under their HD logo. */
function TopCard({ artist, rank, mostPlayed, ...actions }: SuggestionProps) {
  const art = artist.art
  return (
    <li className="relative h-80 overflow-hidden rounded-2xl border border-border bg-surface-2">
      {art?.background && <img src={art.background} alt="" loading="lazy" className="absolute inset-0 size-full object-cover" />}
      <div aria-hidden className="absolute inset-0 bg-gradient-to-b from-black/15 via-black/35 to-black/90" />
      <div className="absolute inset-0 flex flex-col p-4 text-[#f1efe9]">
        <div className="flex items-start justify-between gap-2">
          <span className="flex size-8 items-center justify-center rounded-full bg-black/55 font-display text-sm font-bold">{rank}</span>
          <Actions artist={artist} {...actions} onArt />
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center px-2 py-3">
          {art?.logo ? (
            <img src={art.logo} alt={artist.name} className="max-h-28 max-w-full object-contain drop-shadow-[0_2px_12px_rgba(0,0,0,0.6)]" />
          ) : (
            <h3 className="text-center font-display text-4xl leading-none font-bold drop-shadow-[0_2px_12px_rgba(0,0,0,0.6)]">{artist.name}</h3>
          )}
        </div>
        <div className="space-y-1 text-xs">
          {art?.logo && <h3 className="sr-only">{artist.name}</h3>}
          <p className="text-sm font-semibold">Similar to {formatCount(artist.similar_to)} of your artists</p>
          <p className="line-clamp-2 text-[#d3d0c8]">
            <Sources artist={artist} linkClass="text-[#f1efe9]" />
          </p>
          {artist.listens > 0 && <PlaysBar plays={artist.listens} most={mostPlayed} name={artist.name} />}
        </div>
      </div>
    </li>
  )
}

/** 4–10: a row with the artist's fanart.tv banner (or their name, when there's none). */
function BannerRow({ artist, rank, mostPlayed, ...actions }: SuggestionProps) {
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3 text-sm">
      <span className="w-7 shrink-0 text-right text-xs tabular-nums text-muted">{rank}</span>
      <div className="flex h-14 w-full shrink-0 items-center justify-center overflow-hidden rounded-lg bg-surface-2 sm:w-72" aria-hidden>
        {artist.art?.banner ? (
          <img src={artist.art.banner} alt="" loading="lazy" className="size-full object-cover" />
        ) : (
          <span className="truncate px-3 font-display text-lg font-bold text-muted">{artist.name}</span>
        )}
      </div>
      <div className="min-w-0 flex-[1_1_16rem] space-y-0.5">
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold">{artist.name}</span>
          <span className="text-xs text-muted">similar to {formatCount(artist.similar_to)} of your artists</span>
        </p>
        <p className="truncate text-xs text-muted">
          <Sources artist={artist} />
        </p>
        {artist.listens > 0 && <PlaysBar plays={artist.listens} most={mostPlayed} name={artist.name} />}
      </div>
      <Actions artist={artist} {...actions} />
    </li>
  )
}

/** 11 on (and hidden ones): a plain row. */
function Suggestion({ artist, mostPlayed, rank, ...actions }: SuggestionProps) {
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
          <Sources artist={artist} />
        </p>
      </div>
      <Actions artist={artist} {...actions} />
    </li>
  )
}
