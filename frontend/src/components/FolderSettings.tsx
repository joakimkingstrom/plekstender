import { useState } from 'react'
import { useFolderSettings, useSetFolderSettings } from '../api/artwork'
import { errorMessage } from '../api/client'
import { Button, Card, Notice, Spinner } from './ui'

/**
 * The music folder (server owners): where Plex's music paths are in the worker container,
 * for tag scans and saving artist.jpg into artist folders. Only the worker touches music
 * files, so the worker checks the mapping.
 */
export function FolderSettings({ serverId }: { serverId: number }) {
  const current = useFolderSettings(serverId)
  const save = useSetFolderSettings(serverId)
  const [form, setForm] = useState<{ plex: string; local: string; artistJpg: boolean } | null>(null)
  if (current.isPending) return <Spinner />
  if (current.isError) return <Notice tone="error">{errorMessage(current.error)}</Notice>
  const data = current.data
  const plex = form?.plex ?? data.plex_prefix ?? data.plex_root ?? ''
  const local = form?.local ?? data.local_prefix ?? ''
  const artistJpg = form?.artistJpg ?? data.enabled
  const mapped = Boolean(data.plex_prefix && data.local_prefix)
  const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm'
  const set = (patch: Partial<{ plex: string; local: string; artistJpg: boolean }>) =>
    setForm({ plex, local, artistJpg, ...patch })

  return (
    <Card title="Music folder">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Mount your music folder read-write into the <strong>worker</strong> container (only the worker touches music
          files; see <code>compose.override.example.yaml</code>), then say where Plex's paths are there.
        </p>
        <label className="block">
          <span className="mb-1 block font-medium">Music folder as Plex sees it</span>
          <input value={plex} onChange={(e) => set({ plex: e.target.value })} className={field} placeholder="/music" />
        </label>
        <label className="block">
          <span className="mb-1 block font-medium">Same folder in the worker container</span>
          <input value={local} onChange={(e) => set({ local: e.target.value })} className={field} placeholder="/music" />
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={artistJpg} onChange={(e) => set({ artistJpg: e.target.checked })} />
          Also save artist images as <code>artist.jpg</code> in each artist's folder
        </label>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={!form}
            busy={save.isPending}
            onClick={() =>
              save.mutate(
                { enabled: artistJpg, plex_prefix: plex.trim() || null, local_prefix: local.trim() || null },
                { onSuccess: () => setForm(null) },
              )
            }
          >
            Save
          </Button>
          {mapped && (
            <Button
              variant="ghost"
              busy={save.isPending}
              onClick={() => save.mutate({ enabled: false, plex_prefix: null, local_prefix: null }, { onSuccess: () => setForm(null) })}
            >
              Forget the folder
            </Button>
          )}
        </div>
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
        {mapped && data.checking && (
          <p className="flex items-center gap-2 text-xs text-muted" role="status">
            <Spinner small /> The worker is checking the folder…
          </p>
        )}
        {mapped && !data.checking && data.problem && <Notice tone="error">{data.problem}</Notice>}
        {mapped && !data.checking && data.example && (
          <p className={`text-xs ${data.example.problem ? 'text-danger' : 'text-muted'}`}>
            Check with {data.example.artist}:{' '}
            {data.example.problem ?? `${data.example.local_folder} ✓`}
          </p>
        )}
      </div>
    </Card>
  )
}
