import { errorMessage } from '../api/client'
import { useCoverTagsPreview } from '../api/coverSongs'
import { useWriteCoverTags } from '../api/files'
import { formatCount } from '../format'
import { PlannedChanges } from './PlexSync'
import { Button, Notice } from './ui'

/**
 * Writing whose song a cover is into the files ("Original artist": ID3's TOPE frame,
 * ORIGINALARTIST in FLAC, Ogg and M4A), from the covers found: check what would change, then
 * write it as one edit (undo it as a whole from the history).
 */
export function CoverTags({ serverId, canWrite }: { serverId: number; canWrite: boolean }) {
  const preview = useCoverTagsPreview(serverId)
  const write = useWriteCoverTags(serverId)
  const data = preview.data
  return (
    <div className="space-y-3 text-sm">
      <Button variant="secondary" busy={preview.isPending} onClick={() => preview.mutate()}>
        Check what would change
      </Button>
      {preview.isError && <Notice tone="error">{errorMessage(preview.error)}</Notice>}
      {data &&
        (data.changes === 0 ? (
          <Notice>The covers’ files already name their original artists (or no covers are known yet).</Notice>
        ) : (
          <div className="space-y-3">
            <PlannedChanges data={data} />
            {canWrite ? (
              <Button variant="primary" busy={write.isPending} onClick={() => write.mutate(undefined, { onSuccess: () => preview.reset() })}>
                Write {formatCount(data.changes)} {data.changes === 1 ? 'change' : 'changes'}
              </Button>
            ) : (
              <Notice>Allow changing files (above) to write these.</Notice>
            )}
            {write.isError && <Notice tone="error">{errorMessage(write.error)}</Notice>}
          </div>
        ))}
      {write.isSuccess && !data && (
        <Notice>Writing in the background: follow it in the tag history, where it can be undone as a whole.</Notice>
      )}
    </div>
  )
}
