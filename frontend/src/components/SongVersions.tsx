import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useSongVersions } from '../api/library'
import { formatCount, formatDuration } from '../format'
import { Dialog } from './Dialog'
import { Artwork } from './library'
import { PlayButton } from './PlayButton'
import { RateStars } from './RateStars'
import { Notice, Spinner } from './ui'

/** Every release of a song by the same artist: the album versions, remasters, live and demo
 * versions, oldest first, each with your rating and plays, a preview, and its album. */
export function SongVersions({
  serverId,
  ratingKey,
  title,
  onClose,
}: {
  serverId: number
  ratingKey: number
  title: string
  onClose: () => void
}) {
  const found = useSongVersions(serverId, ratingKey)
  const versions = found.data?.versions ?? []
  return (
    <Dialog
      title={found.data?.title ?? title}
      subtitle={
        found.data
          ? `${found.data.artist ? `${found.data.artist} · ` : ''}${formatCount(versions.length)} ${versions.length === 1 ? 'version' : 'versions'}`
          : undefined
      }
      onClose={onClose}
    >
      {found.isPending ? (
        <Spinner />
      ) : found.isError ? (
        <Notice tone="error">{errorMessage(found.error)}</Notice>
      ) : (
        <ol className="divide-y divide-border">
          {versions.map((v) => (
            <li key={v.rating_key} className={`flex items-center gap-3 py-2 text-sm ${v.current ? 'rounded-lg bg-surface-2/60 px-2' : ''}`}>
              <Artwork serverId={serverId} path={v.album_thumb} title={v.album_title ?? v.title} size={96} className="size-10 shrink-0 rounded" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">
                  {v.title}
                  {v.current && <span className="sr-only"> (this one)</span>}
                </div>
                <div className="truncate text-xs text-muted">
                  {v.album_rating_key ? (
                    <Link to={`/library/albums/${v.album_rating_key}`} onClick={onClose} className="hover:text-text hover:underline">
                      {v.album_title}
                    </Link>
                  ) : (
                    v.album_title
                  )}
                  {[v.year, v.release_type && v.release_type !== 'Album' ? v.release_type : null]
                    .filter(Boolean)
                    .map((part) => ` · ${part}`)
                    .join('')}
                </div>
              </div>
              <span className="hidden w-12 text-right text-xs text-muted tabular-nums sm:inline">
                {v.plays ? `${formatCount(v.plays)}×` : ''}
              </span>
              <RateStars serverId={serverId} ratingKey={v.rating_key} value={v.user_rating} label={`Rate ${v.title} (${v.album_title ?? ''})`} size="size-4" />
              <span className="w-10 text-right text-xs text-muted tabular-nums">{formatDuration(v.duration_ms)}</span>
              <PlayButton serverId={serverId} ratingKey={v.rating_key} title={v.title} size="size-7" />
            </li>
          ))}
        </ol>
      )}
    </Dialog>
  )
}
