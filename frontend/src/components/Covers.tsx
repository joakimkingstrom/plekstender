import { useState } from 'react'
import { Link } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useArtistCovers, useChooseOriginal, useCoverOriginals } from '../api/library'
import { formatCount } from '../format'
import { SongVersions } from './SongVersions'
import { Tooltip } from './Tooltip'
import { Dialog } from './Dialog'
import { Artwork } from './library'
import { Button, Card, Notice, Spinner } from './ui'

type Cover = Schemas['CoverOf']

const SITES: Record<string, string> = { secondhandsongs: 'SecondHandSongs', musicbrainz: 'MusicBrainz' }

/** A song in the library: its album, or every release of it when there's more than one. */
function SongLink({
  serverId,
  title,
  ratingKey,
  albumKey,
  versions,
}: {
  serverId: number
  title: string
  ratingKey: number | null | undefined
  albumKey: number | null | undefined
  versions: number
}) {
  const [open, setOpen] = useState(false)
  if (ratingKey && versions > 1)
    return (
      <>
        <button type="button" onClick={() => setOpen(true)} className="hover:text-text hover:underline" title="Every version of this song">
          {title}
        </button>
        {open && <SongVersions serverId={serverId} ratingKey={ratingKey} title={title} onClose={() => setOpen(false)} />}
      </>
    )
  if (albumKey)
    return (
      <Link to={`/library/albums/${albumKey}`} className="hover:text-text hover:underline">
        {title}
      </Link>
    )
  return <>{title}</>
}

/** An artist's page when they're in the library. */
function ArtistLink({ name, ratingKey }: { name: string; ratingKey: number | null | undefined }) {
  return ratingKey ? (
    <Link to={`/library/artists/${ratingKey}`} className="hover:text-text hover:underline">
      {name}
    </Link>
  ) : (
    <>{name}</>
  )
}

/** The cover itself on the site that said it's one. */
function SourceLink({ url, source }: { url: string | null | undefined; source: string }) {
  if (!url) return null
  const label = `This cover on ${SITES[source] ?? source}`
  return (
    <Tooltip content={label} className="shrink-0">
      <a href={url} target="_blank" rel="noreferrer" aria-label={label} className="block rounded p-0.5 text-muted hover:text-text">
        <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M14 4h6v6" />
          <path d="M20 4l-9 9" />
          <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
        </svg>
      </a>
    </Tooltip>
  )
}

/** "Cover of Nine Inch Nails · Hurt", artist first: the artist's page and the song (its album,
 * or its versions) when they're in the library, and an icon to the cover on its source. With
 * no original artist (MusicBrainz doesn't name one), the song's title opens the library's
 * songs of that title, to choose the original from. */
export function CoverOf({ serverId, trackKey, cover }: { serverId: number; trackKey: number; cover: Cover }) {
  const [choosing, setChoosing] = useState(false)
  return (
    <span className="flex min-w-0 items-center gap-1 text-xs text-muted">
      <span className="truncate">
        Cover of{' '}
        {cover.artist && <ArtistLink name={cover.artist} ratingKey={cover.artist_rating_key} />}
        {cover.artist && cover.title && ' · '}
        {cover.title &&
          (cover.artist ? (
            <SongLink
              serverId={serverId}
              title={cover.title}
              ratingKey={cover.original_rating_key}
              albumKey={cover.original_album_rating_key}
              versions={cover.original_versions ?? 0}
            />
          ) : (
            <button type="button" onClick={() => setChoosing(true)} className="hover:text-text hover:underline" title="Songs with this title in the library">
              {cover.title}
            </button>
          ))}
      </span>
      <SourceLink url={cover.cover_url ?? cover.url} source={cover.source} />
      {choosing && <Originals serverId={serverId} trackKey={trackKey} onClose={() => setChoosing(false)} />}
    </span>
  )
}

/** The library's songs with a cover's song title, by other artists: each links to its album,
 * and admins can mark the one it's a cover of (for everyone). */
function Originals({ serverId, trackKey, onClose }: { serverId: number; trackKey: number; onClose: () => void }) {
  const found = useCoverOriginals(serverId, trackKey)
  const choose = useChooseOriginal(serverId, trackKey)
  const title = found.data?.title ?? ''
  const songs = found.data?.songs ?? []
  const chosen = songs.some((s) => s.original)
  return (
    <Dialog
      title={title ? `Songs called “${title}”` : 'Songs with this title'}
      subtitle={found.data?.can_choose ? 'In the library, by other artists: choose the one this is a cover of.' : 'In the library, by other artists.'}
      onClose={onClose}
    >
      {found.isPending ? (
        <Spinner />
      ) : found.isError ? (
        <Notice tone="error">{errorMessage(found.error)}</Notice>
      ) : songs.length === 0 ? (
        <p className="text-sm text-muted">No other artist’s song with this title in the library.</p>
      ) : (
        <div className="space-y-3">
          <ul className="divide-y divide-border" aria-label="Songs with this title">
            {songs.map((s) => (
              <li key={s.rating_key} className="flex items-center gap-3 py-2 text-sm">
                <Artwork serverId={serverId} path={s.album_thumb} title={s.album_title ?? s.title} size={96} className="size-10 shrink-0 rounded" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">
                    {s.artist && (
                      <>
                        <ArtistLink name={s.artist} ratingKey={s.artist_rating_key} />
                        {' · '}
                      </>
                    )}
                    {s.title}
                  </span>
                  {s.album_rating_key && (
                    <Link to={`/library/albums/${s.album_rating_key}`} className="block truncate text-xs text-muted hover:text-text hover:underline">
                      {[s.album_title, s.year].filter(Boolean).join(' · ')}
                    </Link>
                  )}
                </span>
                {s.original ? (
                  <span className="shrink-0 rounded-full bg-accent/15 px-2 py-0.5 text-xs font-medium text-accent">The original</span>
                ) : (
                  found.data.can_choose && (
                    <Button size="xs" variant="ghost" busy={choose.isPending && choose.variables === s.rating_key} onClick={() => choose.mutate(s.rating_key)}>
                      This is the original
                    </Button>
                  )
                )}
              </li>
            ))}
          </ul>
          {chosen && found.data.can_choose && (
            <Button size="xs" variant="ghost" busy={choose.isPending && choose.variables === null} onClick={() => choose.mutate(null)}>
              None of these
            </Button>
          )}
          {choose.isError && <Notice tone="error">{errorMessage(choose.error)}</Notice>}
        </div>
      )}
    </Dialog>
  )
}

const SHOWN = 8

/** On an artist's page: the covers they recorded, and their songs others covered, as Cover
 * songs found them in the library. Nothing when there are none. */
export function ArtistCovers({ serverId, artistKey }: { serverId: number; artistKey: number }) {
  const found = useArtistCovers(serverId, artistKey)
  const [all, setAll] = useState(false)
  const covers = found.data?.covers ?? []
  const covered = found.data?.covered ?? []
  if (covers.length === 0 && covered.length === 0) return null
  const year = (y: number | null | undefined) => (y ? <span className="text-xs text-muted"> · {y}</span> : null)
  const more = covers.length > SHOWN || covered.length > SHOWN
  return (
    <Card title="Covers">
      <div className="space-y-4 text-sm">
        {covers.length > 0 && (
          <section>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
              Their covers ({formatCount(covers.length)})
            </h3>
            <ul className="space-y-1.5" aria-label="Their covers">
              {(all ? covers : covers.slice(0, SHOWN)).map((c) => (
                <li key={c.rating_key} className="min-w-0">
                  <span className="block truncate">
                    <SongLink serverId={serverId} title={c.title} ratingKey={c.rating_key} albumKey={c.album_rating_key} versions={1} />
                    {year(c.year)}
                  </span>
                  <CoverOf serverId={serverId} trackKey={c.rating_key} cover={c.cover_of} />
                </li>
              ))}
            </ul>
          </section>
        )}
        {covered.length > 0 && (
          <section>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
              Covered by others ({formatCount(covered.length)})
            </h3>
            <ul className="space-y-1.5" aria-label="Covered by others">
              {(all ? covered : covered.slice(0, SHOWN)).map((c) => (
                <li key={c.rating_key} className="min-w-0">
                  <span className="flex min-w-0 items-center gap-1">
                    <span className="truncate">
                      {c.artist && (
                        <>
                          <ArtistLink name={c.artist} ratingKey={c.artist_rating_key} />
                          {' · '}
                        </>
                      )}
                      <SongLink serverId={serverId} title={c.title} ratingKey={c.rating_key} albumKey={c.album_rating_key} versions={1} />
                      {year(c.year)}
                    </span>
                    <SourceLink url={c.cover_url} source={c.source} />
                  </span>
                  {c.original_title && (
                    <span className="block truncate text-xs text-muted">
                      Their version:{' '}
                      <SongLink
                        serverId={serverId}
                        title={c.original_title}
                        ratingKey={c.original_rating_key}
                        albumKey={c.original_album_rating_key}
                        versions={c.original_versions ?? 0}
                      />
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
        {more && (
          <button type="button" onClick={() => setAll((a) => !a)} className="text-xs text-accent hover:underline">
            {all ? 'Show fewer' : 'Show all'}
          </button>
        )}
      </div>
    </Card>
  )
}
