import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { errorMessage } from '../api/client'
import { useTag, type TagSort } from '../api/library'
import { Artwork } from '../components/library'
import { RateStars } from '../components/RateStars'
import { Select } from '../components/Select'
import { TagChip } from '../components/TagChip'
import { KIND_LABELS } from '../tagKinds'
import { Card, Notice, Spinner } from '../components/ui'
import { useLibraryServer } from '../components/useLibraryServer'
import { formatCount } from '../format'

const SORTS: { value: TagSort; label: string }[] = [
  { value: 'plays', label: 'Your plays' },
  { value: 'rating', label: 'Your rating' },
  { value: 'year', label: 'Year' },
  { value: 'name', label: 'Name' },
]

function plural(n: number, one: string, many: string) {
  return `${formatCount(n)} ${n === 1 ? one : many}`
}

/** /library/tags/:id — a genre's, style's or mood's artists and albums. */
export function TagPage() {
  const { blocker, active } = useLibraryServer()
  const id = Number(useParams().id)
  if (blocker) return blocker
  return <TagView serverId={active!.id} tagId={id} />
}

function TagView({ serverId, tagId }: { serverId: number; tagId: number }) {
  const [sort, setSort] = useState<TagSort>('plays')
  const tag = useTag(serverId, tagId, sort)
  if (tag.isPending) return <Spinner />
  if (tag.isError) return <Notice tone="error">{errorMessage(tag.error)}</Notice>
  const t = tag.data
  const kind = KIND_LABELS[t.kind] ?? t.kind

  return (
    <>
      <Link to="/library" className="mb-4 inline-block text-sm text-muted hover:text-text">
        ← Library
      </Link>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted">{kind}</p>
          <h1 className="text-3xl font-bold tracking-tight">{t.name}</h1>
          <p className="mt-1 text-sm text-muted">
            {plural(t.artist_count, 'artist', 'artists')} · {plural(t.album_count, 'album', 'albums')}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select label="Sort by" value={sort} onChange={setSort} options={SORTS} />
          {t.artist_count > 0 && (
            <Link
              to={`/library/playlists/new?${new URLSearchParams({ tag: String(t.id), name: t.name, kind: t.kind })}`}
              className="inline-flex items-center rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-on-accent hover:bg-accent-strong"
            >
              Make a playlist
            </Link>
          )}
        </div>
      </div>

      {t.related.length > 0 && (
        <section className="mb-6" aria-labelledby="related-tags">
          <h2 id="related-tags" className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
            Often found with
          </h2>
          <div className="flex flex-wrap gap-1.5">
            {t.related.map((r) => (
              <TagChip key={r.id} tag={r} />
            ))}
          </div>
        </section>
      )}

      <div className="space-y-6">
        {t.artists.length > 0 && (
          <Card title={`Artists (${formatCount(t.artist_count)})`}>
            <ul className="grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8">
              {t.artists.map((a) => (
                <li key={a.rating_key}>
                  <Link to={`/library/artists/${a.rating_key}`} className="group block">
                    <Artwork serverId={serverId} path={a.thumb} title={a.title} round className="w-full group-hover:opacity-85" />
                    <div className="mt-1.5 truncate text-center text-sm font-medium">{a.title}</div>
                  </Link>
                  <p className="text-center text-xs text-muted tabular-nums">
                    {a.plays > 0 ? plural(a.plays, 'play', 'plays') : ' '}
                  </p>
                </li>
              ))}
            </ul>
            {t.artist_count > t.artists.length && (
              <p className="mt-3 text-xs text-muted">The first {formatCount(t.artists.length)} are shown.</p>
            )}
          </Card>
        )}
        {t.albums.length > 0 && (
          <Card title={`Albums (${formatCount(t.album_count)})`}>
            <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
              {t.albums.map((al) => (
                <li key={al.rating_key}>
                  <Link to={`/library/albums/${al.rating_key}`} className="group block">
                    <Artwork serverId={serverId} path={al.thumb} title={al.title} className="w-full group-hover:opacity-85" />
                    <div className="mt-2 truncate text-sm font-medium">{al.title}</div>
                  </Link>
                  <div className="truncate text-xs text-muted">
                    {[al.artist_title, al.year].filter(Boolean).join(' · ')}
                  </div>
                  <RateStars serverId={serverId} ratingKey={al.rating_key} value={al.user_rating} label={`Rate ${al.title}`} size="size-3.5" />
                </li>
              ))}
            </ul>
            {t.album_count > t.albums.length && (
              <p className="mt-3 text-xs text-muted">The first {formatCount(t.albums.length)} are shown.</p>
            )}
          </Card>
        )}
        {t.artist_count === 0 && t.album_count === 0 && (
          <p className="text-sm text-muted">Nothing in your libraries has this {kind.toLowerCase()}.</p>
        )}
      </div>
    </>
  )
}
