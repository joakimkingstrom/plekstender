import { useState } from 'react'
import { useAlbumNfo, useArtistNfo, type Nfo } from '../api/files'
import { Button, Card, Notice } from './ui'

const short = (id: string) => `${id.slice(0, 8)}…`

function MbLink({ page, id }: { page: string; id: string }) {
  return (
    <a
      href={`https://musicbrainz.org/${page}/${id}`}
      target="_blank"
      rel="noreferrer"
      title={id}
      className="font-mono underline decoration-dotted underline-offset-2 hover:text-accent"
    >
      {short(id)}
    </a>
  )
}

function Chips({ nfo }: { nfo: Nfo }) {
  const groups: [string, unknown][] = [
    ['Genres', nfo.data.genres],
    ['Styles', nfo.data.styles],
    ['Moods', nfo.data.moods],
    ['Themes', nfo.data.themes],
  ]
  const shown = groups.filter(([, v]) => Array.isArray(v) && v.length > 0) as [string, string[]][]
  if (!shown.length) return null
  return (
    <dl className="space-y-1.5">
      {shown.map(([label, values]) => (
        <div key={label} className="flex flex-wrap items-baseline gap-1.5">
          <dt className="w-16 shrink-0 text-xs text-muted">{label}</dt>
          {values.map((v) => (
            <dd key={v} className="rounded-full bg-surface-2 px-2.5 py-0.5 text-xs">
              {v}
            </dd>
          ))}
        </div>
      ))}
    </dl>
  )
}

/** A long text (review, biography): a few lines, then "More". */
function LongText({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const long = text.length > 400
  return (
    <div>
      <p className={`whitespace-pre-line text-muted ${long && !open ? 'line-clamp-4' : ''}`}>{text}</p>
      {long && (
        <button type="button" onClick={() => setOpen(!open)} className="mt-1 text-sm font-medium text-accent">
          {open ? 'Less' : 'More'}
        </button>
      )}
    </div>
  )
}

function Facts({ facts }: { facts: [string, unknown][] }) {
  const shown = facts.filter(([, v]) => typeof v === 'string' && v)
  if (!shown.length) return null
  return (
    <p className="flex flex-wrap gap-x-4 gap-y-1">
      {shown.map(([label, v]) => (
        <span key={label}>
          <span className="text-muted">{label}</span> {String(v)}
        </span>
      ))}
    </p>
  )
}

function Title({ nfo }: { nfo: Nfo }) {
  return (
    <span>
      From {nfo.kind}.nfo{' '}
      <span className="block truncate font-mono text-xs font-normal text-muted" title={nfo.path}>
        {nfo.path}
      </span>
    </span>
  )
}

/** What the album's album.nfo says, and its MusicBrainz release ready to apply. */
export function AlbumNfo({
  serverId,
  albumKey,
  onApplyRelease,
}: {
  serverId: number
  albumKey: number
  onApplyRelease: (releaseId: string) => void
}) {
  const nfo = useAlbumNfo(serverId, albumKey)
  if (!nfo.data) return null
  const d = nfo.data.data
  const release = typeof d.musicbrainz_albumid === 'string' ? d.musicbrainz_albumid : null
  const files = nfo.data.file_release_ids ?? []
  const same = release !== null && files.length === 1 && files[0] === release
  return (
    <Card title={<Title nfo={nfo.data} />}>
      <div className="space-y-3 text-sm">
        {nfo.data.error && <Notice tone="error">Couldn't read it: {nfo.data.error}</Notice>}
        {release && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span>
              <span className="text-muted">MusicBrainz release</span> <MbLink page="release" id={release} />
            </span>
            <span className={`text-xs ${same ? 'text-ok' : 'text-muted'}`}>
              {same
                ? 'Your files have the same release.'
                : files.length === 0
                  ? "Your files don't say which release they are."
                  : `Your files say ${files.length === 1 ? 'another release' : 'several releases'} (${files.map(short).join(', ')}).`}
            </span>
            {!same && (
              <Button variant="secondary" size="sm" onClick={() => onApplyRelease(release)}>
                Use this release…
              </Button>
            )}
          </div>
        )}
        <Facts
          facts={[
            ['Released', d.release_date ?? d.year],
            ['Label', d.label],
            ['Type', d.type],
          ]}
        />
        <Chips nfo={nfo.data} />
        {typeof d.review === 'string' && <LongText text={d.review} />}
      </div>
    </Card>
  )
}

/** What the artist's artist.nfo says. */
export function ArtistNfo({ serverId, artistKey }: { serverId: number; artistKey: number }) {
  const nfo = useArtistNfo(serverId, artistKey)
  if (!nfo.data) return null
  const d = nfo.data.data
  const mbid = typeof d.musicbrainz_artistid === 'string' ? d.musicbrainz_artistid : null
  const plex = nfo.data.plex_mbid
  return (
    <Card title={<Title nfo={nfo.data} />}>
      <div className="space-y-3 text-sm">
        {nfo.data.error && <Notice tone="error">Couldn't read it: {nfo.data.error}</Notice>}
        {mbid && (
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>
              <span className="text-muted">MusicBrainz artist</span> <MbLink page="artist" id={mbid} />
            </span>
            {plex && (
              <span className={`text-xs ${plex.toLowerCase() === mbid ? 'text-ok' : 'text-warn'}`}>
                {plex.toLowerCase() === mbid ? 'The same as Plex.' : `Plex has another one (${short(plex)}).`}
              </span>
            )}
          </p>
        )}
        <Facts
          facts={[
            ['Born / formed', d.born],
            ['Died / disbanded', d.died],
            ['Active', d.years_active],
            ['Type', d.type],
          ]}
        />
        <Chips nfo={nfo.data} />
        {typeof d.biography === 'string' && <LongText text={d.biography} />}
      </div>
    </Card>
  )
}
