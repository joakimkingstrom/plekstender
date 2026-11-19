import { useState } from 'react'
import { useFolderSettings, useSetFolderSettings } from '../api/artwork'
import { errorMessage } from '../api/client'
import { Button, Card, Notice, Spinner } from './ui'

/** Also save finished images as artist.jpg in each artist folder (server owners). */
export function FolderSettings({ serverId }: { serverId: number }) {
  const current = useFolderSettings(serverId)
  const save = useSetFolderSettings(serverId)
  const [form, setForm] = useState<{ plex: string; local: string } | null>(null)
  if (current.isPending || current.isError) return null
  const data = current.data
  const plex = form?.plex ?? data.plex_prefix ?? data.plex_root ?? ''
  const local = form?.local ?? data.local_prefix ?? ''
  const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm'
  return (
    <Card
      title={
        <>
          Save to artist folders{' '}
          {data.enabled ? <span className="font-normal text-ok">· on</span> : <span className="font-normal text-muted">· off</span>}
        </>
      }
    >
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Also writes <code>artist.jpg</code> into each artist's music folder, for other players. Mount your music
          folder read-write into the <strong>worker</strong> container (only the worker touches music files; see{' '}
          <code>compose.override.example.yaml</code>), then map Plex's path to it.
        </p>
        <label className="block">
          <span className="mb-1 block font-medium">Music folder as Plex sees it</span>
          <input value={plex} onChange={(e) => setForm({ plex: e.target.value, local })} className={field} placeholder="/music" />
        </label>
        <label className="block">
          <span className="mb-1 block font-medium">Same folder in Plekstender's container</span>
          <input value={local} onChange={(e) => setForm({ plex, local: e.target.value })} className={field} placeholder="/music" />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            busy={save.isPending}
            onClick={() => save.mutate({ enabled: true, plex_prefix: plex, local_prefix: local }, { onSuccess: () => setForm(null) })}
          >
            {data.enabled ? 'Update' : 'Turn on'}
          </Button>
          {data.enabled && (
            <Button variant="ghost" busy={save.isPending} onClick={() => save.mutate({ enabled: false, plex_prefix: plex, local_prefix: local })}>
              Turn off
            </Button>
          )}
        </div>
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
        {data.enabled && data.checking && (
          <p className="flex items-center gap-2 text-xs text-muted" role="status">
            <Spinner small /> The worker is checking the folder…
          </p>
        )}
        {data.enabled && !data.checking && data.problem && <Notice tone="error">{data.problem}</Notice>}
        {data.enabled && !data.checking && data.example && (
          <p className={`text-xs ${data.example.problem ? 'text-danger' : 'text-muted'}`}>
            Check with {data.example.artist}:{' '}
            {data.example.problem ?? `${data.example.local_folder} ✓`}
          </p>
        )}
      </div>
    </Card>
  )
}
