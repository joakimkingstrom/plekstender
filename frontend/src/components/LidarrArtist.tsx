import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useAddLidarrArtist, useLidarrArtistLookup, type ArtistMonitor } from '../api/lidarr'
import { Dialog } from './Dialog'
import { Select } from './Select'
import { Badge, Button, Notice, Spinner } from './ui'

const MONITOR: { value: ArtistMonitor; label: string }[] = [
  { value: 'future', label: 'New albums, from now on' },
  { value: 'latest', label: 'The latest album, and new ones' },
  { value: 'all', label: 'Every album' },
  { value: 'none', label: 'Nothing yet (just add them)' },
]

/** Discover: add an artist to Lidarr: the right one of those by that name, and which albums. */
export function LidarrArtistDialog({ name, onClose }: { name: string; onClose: () => void }) {
  const found = useLidarrArtistLookup(name)
  const add = useAddLidarrArtist()
  const [picked, setPicked] = useState<string | null>(null)
  const [monitor, setMonitor] = useState<ArtistMonitor>('future')
  const [search, setSearch] = useState(false)
  const candidates = found.data ?? []
  const chosen = picked ?? candidates.find((a) => !a.in_lidarr)?.mbid ?? null
  return (
    <Dialog title={`Add ${name} to Lidarr`} onClose={onClose}>
      <div className="space-y-4 text-sm">
        {add.isSuccess ? (
          <Notice>
            Added {add.data.name} to Lidarr
            {monitor === 'none' ? '.' : search ? ': it\'s looking for their albums now.' : ', monitored.'}
          </Notice>
        ) : found.isPending ? (
          <p className="flex items-center gap-2 text-muted">
            <Spinner small /> Looking them up…
          </p>
        ) : found.isError ? (
          <Notice tone="error">{errorMessage(found.error)}</Notice>
        ) : candidates.length === 0 ? (
          <Notice>Lidarr's metadata doesn't know anyone by that name.</Notice>
        ) : (
          <>
            <fieldset className="space-y-2">
              <legend className="mb-1 font-medium">Which {name}?</legend>
              {candidates.map((a) => (
                <label key={a.mbid} className={`flex items-start gap-3 rounded-lg border p-2.5 ${chosen === a.mbid ? 'border-accent' : 'border-border'}`}>
                  <input
                    type="radio"
                    name="lidarr-artist"
                    className="mt-1"
                    checked={chosen === a.mbid}
                    disabled={a.in_lidarr}
                    onChange={() => setPicked(a.mbid)}
                  />
                  <span className="min-w-0 space-y-0.5">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">{a.name}</span>
                      {(a.disambiguation || a.kind) && <span className="text-muted">{[a.disambiguation, a.kind].filter(Boolean).join(' · ')}</span>}
                      {a.in_lidarr && <Badge>In Lidarr already</Badge>}
                    </span>
                    {a.overview && <span className="block text-xs text-muted">{a.overview}</span>}
                    <a href={`https://musicbrainz.org/artist/${a.mbid}`} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">
                      On MusicBrainz
                    </a>
                  </span>
                </label>
              ))}
            </fieldset>
            <div className="flex flex-wrap items-end gap-4">
              <div className="space-y-1">
                <span id="lidarr-monitor" className="block text-xs font-medium text-muted">
                  Monitor
                </span>
                <Select labelledBy="lidarr-monitor" value={monitor} onChange={setMonitor} options={MONITOR} />
              </div>
              {(monitor === 'latest' || monitor === 'all') && (
                <label className="flex items-center gap-2 pb-2">
                  <input type="checkbox" checked={search} onChange={(e) => setSearch(e.target.checked)} />
                  Look for them now
                </label>
              )}
            </div>
            {add.isError && <Notice tone="error">{errorMessage(add.error)}</Notice>}
            <Button
              variant="primary"
              disabled={!chosen}
              busy={add.isPending}
              onClick={() => chosen && add.mutate({ mbid: chosen, monitor, search: search && (monitor === 'latest' || monitor === 'all') })}
            >
              Add to Lidarr
            </Button>
          </>
        )}
      </div>
    </Dialog>
  )
}
