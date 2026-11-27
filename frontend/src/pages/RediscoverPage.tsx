import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useActiveServer } from '../api/library'
import { useRate } from '../api/ratings'
import { useHideSong, useRediscover } from '../api/stats'
import { HideToggle } from '../components/HideToggle'
import { Artwork } from '../components/library'
import { StarRating } from '../components/StarRating'
import { Card, Notice, Spinner } from '../components/ui'
import { formatCount, formatDate } from '../format'

type Song = Schemas['RediscoverSongOut']
type List = Schemas['RediscoverListOut']

function plays(n: number) {
  return `${formatCount(n)} ${n === 1 ? 'play' : 'plays'}`
}

/** 9 → "4½ stars" (ratings are 0-10, half stars). */
function stars(rating: number) {
  const whole = Math.floor(rating / 2)
  return `${whole || ''}${rating % 2 ? '½' : ''} ${rating === 2 ? 'star' : 'stars'}`
}

function SongRow({ song, serverId }: { song: Song; serverId: number }) {
  const rate = useRate(serverId)
  const hide = useHideSong(serverId)
  const detail = [plays(song.plays), song.last_played_at ? `last ${formatDate(song.last_played_at)}` : 'never played']
  return (
    <li className={`flex items-center gap-3 ${song.hidden ? 'opacity-50' : ''}`}>
      <Artwork serverId={serverId} path={song.thumb} title={song.album ?? song.title} size={96} className="size-10 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">
          {song.album_rating_key !== null ? (
            <Link to={`/library/albums/${song.album_rating_key}`} className="hover:underline">
              {song.title}
            </Link>
          ) : (
            song.title
          )}
        </p>
        <p className="truncate text-xs text-muted">
          {song.artist ?? ' '} · {detail.join(' · ')}
        </p>
      </div>
      <StarRating
        label={`Rate ${song.title}`}
        value={song.rating}
        disabled={rate.isPending}
        onChange={(rating) => rate.mutate({ rating_key: song.rating_key, rating })}
      />
      <HideToggle
        hidden={song.hidden}
        name={song.title}
        busy={hide.isPending}
        onToggle={() => hide.mutate({ ratingKey: song.rating_key, hide: !song.hidden })}
      />
    </li>
  )
}

function SongList({
  title,
  intro,
  list,
  empty,
  serverId,
  action,
}: {
  title: string
  intro: string
  list: List
  empty: string
  serverId: number
  action?: ReactNode
}) {
  return (
    <Card title={title} action={list.total > 0 ? action : undefined}>
      <p className="mb-4 text-sm text-muted">{intro}</p>
      {list.items.length === 0 ? (
        <p className="text-sm text-muted">{empty}</p>
      ) : (
        <>
          <ul className="space-y-2.5">
            {list.items.map((song) => (
              <SongRow key={song.rating_key} song={song} serverId={serverId} />
            ))}
          </ul>
          {list.total > list.items.length && (
            <p className="mt-3 text-xs text-muted">
              Showing {formatCount(list.items.length)} of {formatCount(list.total)}.
            </p>
          )}
        </>
      )}
    </Card>
  )
}

/** History → Rediscover: songs whose ratings and plays disagree. */
export function RediscoverPage() {
  const { active } = useActiveServer()
  const serverId = active && active.music_section_keys.length > 0 ? active.id : null
  if (serverId === null) return <Notice tone="warn">Choose a music library first: these are songs in it.</Notice>
  return <Lists serverId={serverId} />
}

function Lists({ serverId }: { serverId: number }) {
  const [includeHidden, setIncludeHidden] = useState(false)
  const found = useRediscover(serverId, includeHidden)
  if (found.isPending) return <Spinner />
  if (found.isError) return <Notice tone="error">{errorMessage(found.error)}</Notice>
  const r = found.data
  const loved = stars(r.loved)
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Songs where your ratings and your plays disagree: either the rating is wrong, or the song is worth finding again.
        Change a rating here and the song leaves the list, or hide it if you'd rather not see it (it's left out of the
        forgotten favourites playlist too). Plays include your whole history, imports too.
      </p>
      <label className="flex items-center gap-2 text-sm text-muted">
        <input type="checkbox" checked={includeHidden} onChange={(e) => setIncludeHidden(e.target.checked)} />
        Show hidden songs
      </label>
      <SongList
        title="Forgotten favourites"
        intro={`Rated ${loved} or more, but not played for a year.`}
        list={r.forgotten}
        empty="None: you keep coming back to the songs you love."
        serverId={serverId}
        action={
          <Link to="/library/playlists/new?forgotten=1" className="text-sm text-accent hover:underline">
            Make a playlist
          </Link>
        }
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 [&>*]:min-w-0">
        <SongList
          title="Loved but hardly played"
          intro={`Rated ${loved} or more, played ${r.hardly_played === 1 ? 'once' : `${r.hardly_played} times`} at most.`}
          list={r.unplayed}
          empty="None: the songs you rate highly get played."
          serverId={serverId}
        />
        <SongList
          title="Played a lot, rated low"
          intro={`Rated ${stars(r.disliked_below)} or less, but played ${r.played_a_lot} times or more.`}
          list={r.disliked}
          empty="None: what you play a lot, you rate well."
          serverId={serverId}
        />
      </div>
    </div>
  )
}
