import { Link } from 'react-router'
import type { Schemas } from '../api/client'
import { formatCount } from '../format'
import { Artwork } from './library'
import { Tooltip } from './Tooltip'
import { Card } from './ui'

/** A live album's songs by the studio release each first came out on: one bar per release,
 * oldest first, in its cover's colour (named by its cover and title, so the colour only
 * decorates), each a link to the album. */
export function LiveSources({
  serverId,
  sources,
  unmatched,
}: {
  serverId: number
  sources: Schemas['LiveSource'][]
  unmatched: number
}) {
  if (sources.length === 0) return null
  const max = Math.max(...sources.map((s) => s.songs))
  const total = sources.reduce((n, s) => n + s.songs, 0) + unmatched
  return (
    <Card title="Where the songs come from">
      <ol className="space-y-1.5" aria-label="Songs by studio release">
        {sources.map((s) => {
          const label = `${formatCount(s.songs)} ${s.songs === 1 ? 'song' : 'songs'} from ${s.title}${s.year ? ` (${s.year})` : ''}`
          return (
            <li key={s.rating_key}>
              <Tooltip content={label} className="block">
                <Link to={`/library/albums/${s.rating_key}`} aria-label={label} className="flex items-center gap-2 rounded-lg p-1 hover:bg-surface-2/60">
                  <Artwork serverId={serverId} path={s.thumb} title={s.title} size={96} className="size-8 shrink-0 rounded" />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-2 text-sm">
                      <span className="truncate">
                        {s.title}
                        <span className="text-xs text-muted">
                          {[s.year, s.release_type !== 'Album' ? s.release_type : null].filter(Boolean).map((p) => ` · ${p}`).join('')}
                        </span>
                      </span>
                      <span className="shrink-0 text-xs text-muted tabular-nums">{formatCount(s.songs)}</span>
                    </span>
                    <span className="mt-1 block h-1.5 rounded-full bg-surface-2">
                      <span
                        className="block h-full rounded-full ring-1 ring-text/15"
                        style={{ width: `${(100 * s.songs) / max}%`, background: s.colour ?? 'var(--color-accent)' }}
                      />
                    </span>
                  </span>
                </Link>
              </Tooltip>
            </li>
          )
        })}
      </ol>
      {unmatched > 0 && (
        <p className="mt-3 text-xs text-muted">
          {formatCount(unmatched)} of {formatCount(total)} songs aren’t on one of the artist’s studio releases in the
          library (covers, new songs, or releases you don’t have).
        </p>
      )}
    </Card>
  )
}
