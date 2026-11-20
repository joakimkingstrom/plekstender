import { Select } from './Select'
import { useState } from 'react'
import { errorMessage } from '../api/client'
import {
  pictureUrl,
  useAlbumArtwork,
  useArtworkSummary,
  useEmbedCover,
  useSaveFolderCover,
  useShrinkCovers,
  type AlbumArtwork,
} from '../api/files'
import { formatCount } from '../format'
import { Button, Card, Notice } from './ui'

const SIZES: [number, string][] = [
  [600, '600 px (older car stereos)'],
  [1000, '1000 px (most players)'],
  [1500, '1500 px'],
]

function kb(bytes: number) {
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.round(bytes / 1000)} KB`
}

function Picture({
  serverId,
  sha1,
  caption,
  warn,
}: {
  serverId: number
  sha1: string | null | undefined
  caption: string
  warn?: string
}) {
  return (
    <figure className="w-32 space-y-1">
      {sha1 ? (
        <img src={pictureUrl(serverId, sha1)} alt="" className="size-32 rounded-lg bg-surface-2 object-cover" />
      ) : (
        <span aria-hidden className="block size-32 rounded-lg bg-surface-2" />
      )}
      <figcaption className="text-xs text-muted">
        {caption}
        {warn && <span className="block text-warn">{warn}</span>}
      </figcaption>
    </figure>
  )
}

/**
 * An album's artwork in the files: the folder's cover.jpg (and the like) and the covers in
 * its songs, side by side; put one cover in every song, or save a cover.jpg.
 */
export function AlbumArtworkCard({ serverId, albumKey, canWrite }: { serverId: number; albumKey: number; canWrite: boolean }) {
  const art = useAlbumArtwork(serverId, albumKey)
  if (!art.data || art.data.songs === 0) return null
  return (
    <Card title="Artwork in the files">
      <ArtworkBody serverId={serverId} albumKey={albumKey} art={art.data} canWrite={canWrite} />
    </Card>
  )
}

function ArtworkBody({ serverId, albumKey, art, canWrite }: { serverId: number; albumKey: number; art: AlbumArtwork; canWrite: boolean }) {
  const covers = art.folder_images.filter((i) => i.kind === 'cover' && i.sha1 && !i.error)
  const sources: [string, string][] = [
    ...covers.map((i): [string, string] => [`folder:${i.path}`, i.path.split('/').at(-1) ?? i.path]),
    ...(art.plex_poster ? [['plex', "Plex's poster"] as [string, string]] : []),
  ]
  const [source, setSource] = useState(sources[0]?.[0] ?? '')
  const [maxPx, setMaxPx] = useState(1000)
  const embed = useEmbedCover(serverId, albumKey)
  const save = useSaveFolderCover(serverId, albumKey)
  const mostUsed = art.covers[0]
  const hasFolderCover = covers.length > 0
  const allSame = art.covers.length === 1 && art.without_cover === 0

  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap gap-6">
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">In the folder</h3>
          <div className="flex flex-wrap gap-3">
            {art.folder_images.length ? (
              art.folder_images.map((i) => (
                <Picture
                  key={i.path}
                  serverId={serverId}
                  sha1={i.sha1}
                  caption={`${i.path.split('/').at(-1)}${i.width ? ` · ${i.width}×${i.height}` : ''} · ${kb(i.size)}`}
                  warn={i.error ?? undefined}
                />
              ))
            ) : (
              <p className="text-muted">No cover.jpg or folder.jpg.</p>
            )}
          </div>
        </section>
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">In the songs</h3>
          <div className="flex flex-wrap gap-3">
            {art.covers.map((c) => (
              <Picture
                key={c.sha1}
                serverId={serverId}
                sha1={c.sha1}
                caption={`${c.files} of ${art.songs} ${art.songs === 1 ? 'song' : 'songs'}${c.width ? ` · ${c.width}×${c.height}` : ''} · ${kb(c.bytes)}`}
                warn={c.oversized ? 'Too big for some players' : undefined}
              />
            ))}
            {art.without_cover > 0 && (
              <p className="self-center text-muted">
                {art.without_cover === art.songs ? 'No song has a cover.' : `${art.without_cover} without a cover.`}
              </p>
            )}
          </div>
        </section>
      </div>

      {canWrite ? (
        <div className="space-y-3 border-t border-border pt-3">
          {sources.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span>{allSame ? 'Replace the cover in every song with' : 'Put the same cover in every song:'}</span>
              <Select
                size="sm"
                label="Cover to use"
                value={source}
                options={sources.map(([value, label]) => ({ value, label }))}
                onChange={setSource}
              />
              <Select
                size="sm"
                label="Largest size"
                value={maxPx}
                options={SIZES.map(([px, label]) => ({ value: px, label }))}
                onChange={setMaxPx}
              />
              <Button
                variant="secondary"
                className="px-3 py-1.5 text-xs"
                busy={embed.isPending}
                onClick={() =>
                  embed.mutate(
                    source === 'plex'
                      ? { source: 'plex', max_px: maxPx }
                      : { source: 'folder', path: source.slice('folder:'.length), max_px: maxPx },
                  )
                }
              >
                Embed in {art.songs} {art.songs === 1 ? 'song' : 'songs'}
              </Button>
            </div>
          )}
          {!hasFolderCover && (mostUsed || art.plex_poster) && (
            <div className="flex flex-wrap items-center gap-2">
              <span>No cover.jpg yet: save</span>
              {mostUsed && (
                <Button
                  variant="secondary"
                  className="px-3 py-1.5 text-xs"
                  busy={save.isPending && save.variables?.source === 'embedded'}
                  onClick={() => save.mutate({ source: 'embedded', sha1: mostUsed.sha1, name: 'cover.jpg' })}
                >
                  the songs' cover
                </Button>
              )}
              {art.plex_poster && (
                <Button
                  variant="secondary"
                  className="px-3 py-1.5 text-xs"
                  busy={save.isPending && save.variables?.source === 'plex'}
                  onClick={() => save.mutate({ source: 'plex', name: 'cover.jpg' })}
                >
                  Plex's poster
                </Button>
              )}
            </div>
          )}
          <p className="text-xs text-muted">
            Covers put in songs are made JPEGs that players can show (not progressive, at most the size chosen). The
            pictures they replace are kept: undo puts them back.
          </p>
          {(embed.isSuccess || save.isSuccess) && <Notice>Writing in the background: see the tag history below.</Notice>}
          {embed.isError && <Notice tone="error">{errorMessage(embed.error)}</Notice>}
          {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
        </div>
      ) : (
        <p className="text-xs text-muted">Turn on "Allow changing files" (Settings → Music files) to embed or save covers.</p>
      )}
    </div>
  )
}

/** Library-wide: songs without covers, covers too big for players (and shrinking them). */
export function ArtworkSummaryCard({ serverId, canWrite }: { serverId: number; canWrite: boolean }) {
  const summary = useArtworkSummary(serverId)
  const shrink = useShrinkCovers(serverId)
  const [maxPx, setMaxPx] = useState(1000)
  const s = summary.data
  if (!s || s.songs === 0) return null
  return (
    <Card title="Artwork in the files">
      <div className="space-y-3 text-sm">
        <ul className="space-y-1">
          <li>
            {formatCount(s.songs - s.without_cover)} of {formatCount(s.songs)} songs have a cover in the file.
          </li>
          <li>{formatCount(s.folders_without_cover)} folders with songs have no cover.jpg or folder.jpg.</li>
          <li className={s.oversized ? 'text-warn' : undefined}>
            {formatCount(s.oversized)} embedded {s.oversized === 1 ? 'cover is' : 'covers are'} too big for some players
            (over 1000 px or about 1 MB).
          </li>
        </ul>
        {s.oversized > 0 &&
          (canWrite ? (
            <div className="flex flex-wrap items-center gap-2">
              <Select
                size="sm"
                label="Shrink to"
                value={maxPx}
                options={SIZES.map(([px, label]) => ({ value: px, label }))}
                onChange={setMaxPx}
              />
              <Button variant="secondary" busy={shrink.isPending} onClick={() => shrink.mutate(maxPx)}>
                Shrink {formatCount(s.oversized)} {s.oversized === 1 ? 'cover' : 'covers'}
              </Button>
            </div>
          ) : (
            <p className="text-xs text-muted">Allow changing files (above) to shrink them.</p>
          ))}
        {shrink.isSuccess && <Notice>Shrinking in the background: follow it in the tag history.</Notice>}
        {shrink.isError && <Notice tone="error">{errorMessage(shrink.error)}</Notice>}
        <p className="text-xs text-muted">Album pages show each album's covers, and can embed or save them.</p>
      </div>
    </Card>
  )
}
