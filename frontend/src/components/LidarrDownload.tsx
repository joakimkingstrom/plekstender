import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useAddLidarrAlbum, useGrabRelease, useLidarrReleases, type LidarrAlbumState, type LidarrRelease } from '../api/lidarr'
import { formatBytes, formatCount } from '../format'
import { Dialog } from './Dialog'
import { Tooltip } from './Tooltip'
import { Badge, Button, Notice, Spinner } from './ui'

/** What each state says (the icon's tooltip and its name for screen readers). */
function stateText(state: Exclude<LidarrAlbumState, 'absent'>, upcoming: boolean): string {
  if (state === 'downloaded') return 'Downloaded by Lidarr'
  if (state === 'unmonitored') return 'In Lidarr, but not monitored: it won\u2019t be downloaded'
  return upcoming ? 'Monitored by Lidarr: downloaded once it\u2019s out' : 'Monitored by Lidarr'
}

/** How an album stands in Lidarr, as a small icon with a tooltip (nothing when it isn't
 * there): monitored a green bookmark, downloaded a green tick, in Lidarr but not monitored
 * an orange outlined bookmark (something to look at). */
export function LidarrBadge({ state, upcoming = false }: { state: LidarrAlbumState | undefined; upcoming?: boolean }) {
  if (!state || state === 'absent') return null
  const text = stateText(state, upcoming)
  return (
    <Tooltip content={text}>
      <span
        role="img"
        aria-label={text}
        tabIndex={0}
        className={`inline-flex size-7 items-center justify-center rounded-full ${state === 'unmonitored' ? 'bg-accent/15 text-accent' : 'bg-ok/15 text-ok'}`}
      >
        {state === 'downloaded' ? (
          <svg viewBox="0 0 16 16" className="size-4" aria-hidden>
            <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <svg viewBox="0 0 16 16" className="size-4" aria-hidden>
            <path
              d="M4.5 2.5h7v11l-3.5-2.5-3.5 2.5z"
              fill={state === 'monitored' ? 'currentColor' : 'none'}
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </span>
    </Tooltip>
  )
}

/** Coming up: have Lidarr monitor the album, so it grabs it once it's out. */
export function LidarrMonitorButton({ mbid, title }: { mbid: string; title: string }) {
  const add = useAddLidarrAlbum()
  return (
    <>
      <Tooltip content="Lidarr downloads it once it's out">
        <Button variant="ghost" size="xs" busy={add.isPending} onClick={() => add.mutate({ mbid, search: false })} aria-label={`Monitor ${title} in Lidarr`}>
          <LidarrAddIcon />
        </Button>
      </Tooltip>
      {add.isError && <span className="text-xs text-danger">{errorMessage(add.error)}</span>}
    </>
  )
}

/** A bookmark with a plus: add to Lidarr (monitor an album, add an artist). */
export function LidarrAddIcon() {
  return (
    <svg viewBox="0 0 16 16" className="size-4" aria-hidden>
      <path d="M4.5 2.5h7v11l-3.5-2.5-3.5 2.5z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M8 5v4M6 7h4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

export function DownloadIcon() {
  return (
    <svg viewBox="0 0 16 16" className="size-4" aria-hidden>
      <path d="M8 2v8m0 0L4.5 6.5M8 10l3.5-3.5M3 13h10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/**
 * Downloading an album with Lidarr: let Lidarr find the best release (it searches its
 * indexers and grabs what its profile prefers), or choose one from what they have.
 */
export function LidarrDownloadDialog({ mbid, title, artist, onClose }: { mbid: string; title: string; artist?: string; onClose: () => void }) {
  const add = useAddLidarrAlbum()
  const [mode, setMode] = useState<'search' | 'choose' | null>(null)
  const [albumId, setAlbumId] = useState<number | null>(null)
  const start = (search: boolean) => {
    setMode(search ? 'search' : 'choose')
    add.mutate({ mbid, search }, { onSuccess: (album) => !search && setAlbumId(album.id) })
  }
  return (
    <Dialog title={`Download ${title}`} subtitle={artist} onClose={onClose}>
      <div className="space-y-4 text-sm">
        {mode === null ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <ChoiceButton title="Let Lidarr find it" onClick={() => start(true)}>
              Lidarr searches its indexers and downloads the best release its quality profile allows.
            </ChoiceButton>
            <ChoiceButton title="Choose a release" onClick={() => start(false)}>
              See what the indexers have (quality, size, seeders) and pick one. Asking them can take a minute.
            </ChoiceButton>
          </div>
        ) : add.isPending ? (
          <p className="flex items-center gap-2 text-muted">
            <Spinner small /> Adding it to Lidarr…
          </p>
        ) : add.isError ? (
          <Notice tone="error">{errorMessage(add.error)}</Notice>
        ) : mode === 'search' ? (
          <Notice>Added to Lidarr and monitored: it's looking for it now, and downloads the best it finds (or grabs it when it turns up).</Notice>
        ) : (
          <Releases albumId={albumId} />
        )}
      </div>
    </Dialog>
  )
}

function ChoiceButton({ title, children, onClick }: { title: string; children: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="rounded-xl border border-border bg-surface-2 p-3 text-left hover:border-accent">
      <span className="block font-semibold">{title}</span>
      <span className="mt-1 block text-xs text-muted">{children}</span>
    </button>
  )
}

function Releases({ albumId }: { albumId: number | null }) {
  const releases = useLidarrReleases(albumId)
  const grab = useGrabRelease()
  const [grabbed, setGrabbed] = useState<string | null>(null)
  if (albumId === null || releases.isPending)
    return (
      <p className="flex items-center gap-2 text-muted">
        <Spinner small /> Asking Lidarr's indexers (this can take a minute)…
      </p>
    )
  if (releases.isError) return <Notice tone="error">{errorMessage(releases.error)}</Notice>
  if (releases.data.length === 0) return <Notice>The indexers have nothing for it yet. It's monitored, so Lidarr grabs it when it turns up.</Notice>
  return (
    <div className="space-y-2">
      {grabbed && <Notice>Sent to your download client; Lidarr imports it when it's done.</Notice>}
      {grab.isError && <Notice tone="error">{errorMessage(grab.error)}</Notice>}
      <ul className="max-h-[55dvh] divide-y divide-border overflow-y-auto rounded-xl border border-border" aria-label="Releases found">
        {releases.data.map((r) => (
          <ReleaseRow
            key={r.guid}
            release={r}
            busy={grab.isPending && grab.variables?.guid === r.guid}
            done={grabbed === r.guid}
            onGrab={() => grab.mutate({ guid: r.guid, indexer_id: r.indexer_id }, { onSuccess: () => setGrabbed(r.guid) })}
          />
        ))}
      </ul>
      <p className="text-xs text-muted">Releases Lidarr's profile would take come first; the others say why not.</p>
    </div>
  )
}

function ReleaseRow({ release: r, busy, done, onGrab }: { release: LidarrRelease; busy: boolean; done: boolean; onGrab: () => void }) {
  const facts = [
    r.quality,
    r.size ? formatBytes(r.size) : null,
    r.protocol === 'torrent' && r.seeders !== null ? `${formatCount(r.seeders)} seeders` : r.protocol,
    r.age_days !== null ? `${formatCount(r.age_days)} ${r.age_days === 1 ? 'day' : 'days'} old` : null,
    r.indexer,
  ].filter(Boolean)
  return (
    <li className="flex items-start gap-3 p-3">
      <div className="min-w-0 flex-1">
        <p className="break-words font-medium">{r.title}</p>
        <p className="text-xs text-muted">{facts.join(' · ')}</p>
        {!r.approved && r.rejections.length > 0 && <p className="text-xs text-warn">{r.rejections.join('; ')}</p>}
      </div>
      {done ? (
        <Badge tone="ok">Sent</Badge>
      ) : (
        <Button size="sm" variant={r.approved ? 'primary' : 'secondary'} busy={busy} onClick={onGrab}>
          Download
        </Button>
      )}
    </li>
  )
}
