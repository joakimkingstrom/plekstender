import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useRate, useRated, useUndoItem, type RatedItem, type RatedKind } from '../api/ratings'
import { Artwork } from '../components/library'
import { Segmented } from '../components/Segmented'
import { StarRating } from '../components/StarRating'
import { Badge, Button, Card, Notice, Spinner } from '../components/ui'
import { useLibraryServer } from '../components/useLibraryServer'
import { Tooltip } from '../components/Tooltip'
import { starsText } from '../swipe'
import { formatCount, formatDay, formatTime } from '../format'

const KINDS: [RatedKind | 'all', string][] = [
  ['all', 'Everything'],
  ['track', 'Songs'],
  ['album', 'Albums'],
  ['artist', 'Artists'],
]
const KIND_LABEL: Record<RatedKind, string> = { track: 'Song', album: 'Album', artist: 'Artist' }

/** Day headings: Today, Yesterday, then the date (in the viewer's time zone). */
function dayLabel(iso: string | null): string {
  if (!iso) return 'Earlier (no date)'
  const day = new Date(iso)
  const today = new Date()
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const diff = Math.round((start(today) - start(day)) / 86_400_000)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Yesterday'
  return formatDay(iso)
}

export function RatingsPage() {
  const { active, blocker } = useLibraryServer()
  return blocker ?? <RatedList serverId={active!.id} />
}

function RatedList({ serverId }: { serverId: number }) {
  const [kind, setKind] = useState<RatedKind | 'all'>('all')
  const rated = useRated(serverId, kind === 'all' ? null : kind)
  const rate = useRate(serverId)
  const undo = useUndoItem(serverId)

  const items = rated.data?.pages.flatMap((p) => p.items) ?? []
  const total = rated.data?.pages[0]?.total ?? 0
  // Group consecutive items by the day they were rated.
  const groups: { label: string; items: RatedItem[] }[] = []
  for (const item of items) {
    const label = dayLabel(item.rated_at)
    const last = groups.at(-1)
    if (last?.label === label) last.items.push(item)
    else groups.push({ label, items: [item] })
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented label="Show" value={kind} options={KINDS} onChange={setKind} />
        {rated.data && (
          <p className="text-sm text-muted">
            {formatCount(total)} rated {kind === 'all' ? 'items' : KINDS.find(([k]) => k === kind)![1].toLowerCase()}, most recent first
          </p>
        )}
      </div>
      {rate.isError && <Notice tone="error">{errorMessage(rate.error)}</Notice>}
      {undo.isError && <Notice tone="error">{errorMessage(undo.error)}</Notice>}
      {rated.isPending ? (
        <Spinner />
      ) : rated.isError ? (
        <Notice tone="error">{errorMessage(rated.error)}</Notice>
      ) : items.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">
            Nothing rated yet. Ratings you set here or in Plex and Plexamp show up once the library has synced.{' '}
            <Link to="/rate" className="text-accent underline">
              Start rating
            </Link>
          </p>
        </Card>
      ) : (
        <>
          {groups.map((group, g) => (
            <section key={`${group.label}-${g}`} aria-label={group.label}>
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">{group.label}</h2>
              <ol className="divide-y divide-border rounded-2xl border border-border bg-surface">
                {group.items.map((item) => (
                  <RatedRow
                    key={`${item.kind}-${item.rating_key}`}
                    serverId={serverId}
                    item={item}
                    onRate={(rating) => rate.mutate({ rating_key: item.rating_key, rating })}
                    onUndo={() => undo.mutate(item.rating_key)}
                    undoing={undo.isPending && undo.variables === item.rating_key}
                  />
                ))}
              </ol>
            </section>
          ))}
          {rated.hasNextPage && (
            <div className="text-center">
              <Button busy={rated.isFetchingNextPage} onClick={() => void rated.fetchNextPage()}>
                Show more
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function RatedRow({
  serverId,
  item,
  onRate,
  onUndo,
  undoing,
}: {
  serverId: number
  item: RatedItem
  onRate: (rating: number | null) => void
  onUndo: () => void
  undoing: boolean
}) {
  const to =
    item.link_key === null ? null : item.kind === 'artist' ? `/library/artists/${item.link_key}` : `/library/albums/${item.link_key}`
  return (
    <li className="flex items-center gap-3 px-3 py-2">
      <Artwork serverId={serverId} path={item.thumb} title={item.title} size={96} round={item.kind === 'artist'} className="size-10 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">
          {to ? (
            <Link to={to} className="hover:underline">
              {item.title}
            </Link>
          ) : (
            item.title
          )}
        </p>
        <p className="truncate text-xs text-muted">
          {item.subtitle ?? ' '}
        </p>
      </div>
      <span className="hidden sm:inline">
        <Badge>{KIND_LABEL[item.kind]}</Badge>
      </span>
      {item.rated_at && <span className="hidden w-12 text-right text-xs text-muted tabular-nums sm:block">{formatTime(item.rated_at)}</span>}
      <StarRating label={`Rate ${item.title}`} value={item.user_rating} onChange={onRate} />
      {/* Ratings set in Plex (or changed there since) have no undo: what was before isn't known. */}
      <span className="w-14 shrink-0 text-right">
        {item.undoable && (
          <Tooltip content={item.undo_to === null || item.undo_to === undefined ? 'Back to unrated' : `Back to ${starsText(item.undo_to)} ★`}>
            <Button
              variant="ghost"
              className="px-2 py-1 text-xs"
              busy={undoing}
              onClick={onUndo}
              aria-label={`Undo the rating of ${item.title}`}
            >
              ↶ Undo
            </Button>
          </Tooltip>
        )}
      </span>
    </li>
  )
}
