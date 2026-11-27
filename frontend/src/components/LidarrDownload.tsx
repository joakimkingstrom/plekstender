import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useAddLidarrAlbum, useGrabRelease, useLidarrReleases, type LidarrAlbumState, type LidarrRelease } from '../api/lidarr'
import { formatBytes, formatCount } from '../format'
import { Dialog } from './Dialog'
import { Badge, Button, Notice, Spinner } from './ui'

const STATE_TEXT: Record<Exclude<LidarrAlbumState, 'absent'>, string> = {
  monitored: 'In Lidarr',
  unmonitored: 'In Lidarr, not monitored',
  downloaded: 'Downloaded by Lidarr',
}

/** How an album stands in Lidarr, as a small badge (nothing when it isn't there). */
export function LidarrBadge({ state }: { state: LidarrAlbumState | undefined }) {
  if (!state || state === 'absent') return null
  return <Badge tone={state === 'downloaded' ? 'ok' : 'muted'}>{STATE_TEXT[state]}</Badge>
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
