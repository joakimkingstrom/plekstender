import { useState } from 'react'
import { useApplyPack, useArtworkJobs, useStartExport, useUploadPack } from '../api/artwork'
import { errorMessage, type Schemas } from '../api/client'
import { ArtworkJobStop } from './ArtworkJobStatus'
import { FilePicker } from './controls'
import { Button, Card, Notice, Spinner } from './ui'

function JobProgress({ job, label }: { job: Schemas['ArtworkJobOut']; label: string }) {
  const { done = 0, total = 0 } = (job.progress ?? {}) as { done?: number; total?: number }
  return (
    <div className="space-y-2" aria-live="polite">
      <p className="text-xs">
        {label}… <span className="text-muted tabular-nums">{done} / {total || '?'}</span>
      </p>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${total ? (done / total) * 100 : 5}%` }} />
      </div>
      <ArtworkJobStop job={job} />
    </div>
  )
}

/** Export the images of artists marked Done or Custom as a zip (server owners only). */
export function LogoPackExport({ serverId }: { serverId: number }) {
  const jobs = useArtworkJobs(serverId)
  const exportJob = useStartExport(serverId)
  const latestExport = jobs.data?.find((j) => j.kind === 'export')
  return (
    <Card title="Export artist logos">
      <div className="space-y-4 text-sm">
        <div className="space-y-2">
          <p className="text-xs text-muted">
            Export the images of every artist marked Done or Custom as a zip, to back them up or share them. Packs
            match artists by MusicBrainz ID, so they work on other servers too.
          </p>
          {active(latestExport) ? (
            <JobProgress job={latestExport!} label="Exporting" />
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Button busy={exportJob.isPending} onClick={() => exportJob.mutate()}>
                Export
              </Button>
              {latestExport?.download && (
                <a href={latestExport.download} className="text-xs text-accent underline">
                  Download ({String((latestExport.stats as Record<string, number>).exported ?? 0)} artists)
                </a>
              )}
            </div>
          )}
          {exportJob.isError && <Notice tone="error">{errorMessage(exportJob.error)}</Notice>}
        </div>

      </div>
    </Card>
  )
}

/** Import a logo pack: preview the matches, then apply the chosen ones (server owners only). */
export function LogoPackImport({ serverId }: { serverId: number }) {
  const jobs = useArtworkJobs(serverId)
  const upload = useUploadPack(serverId)
  const applyPack = useApplyPack(serverId)
  const [chosen, setChosen] = useState<Set<number>>(new Set())
  const latestImport = jobs.data?.find((j) => j.kind === 'import')
  const preview = upload.data

  const onFile = (file: File) =>
    upload.mutate(file, {
      onSuccess: (data) => setChosen(new Set(data.entries.filter((x) => x.match).map((x) => x.index))),
    })

  return (
    <Card title="Import artist logos">
      <div className="space-y-4 text-sm">
        <p className="text-xs text-muted">
          A pack exported from Plekstender (here or on another server). Artists are matched by MusicBrainz ID, then
          by name; you choose which to apply.
        </p>
        <div className="space-y-2">
          <FilePicker label="Import a pack (.zip)" accept=".zip,application/zip" onFile={onFile} />
          {upload.isPending && <Spinner small />}
          {upload.isError && <Notice tone="error">{errorMessage(upload.error)}</Notice>}
          {active(latestImport) && <JobProgress job={latestImport!} label="Importing" />}
          {preview && !active(latestImport) && (
            <div className="space-y-2">
              <p className="text-xs text-muted">
                {preview.matched} of {preview.total} match your artists. Ticked ones will replace their current image.
              </p>
              <ul className="max-h-72 space-y-1 overflow-y-auto">
                {preview.entries.map((entry) => (
                  <li key={entry.index} className={`flex items-center gap-2 ${entry.match ? '' : 'opacity-50'}`}>
                    <input
                      type="checkbox"
                      aria-label={`Import ${entry.artist_name}`}
                      disabled={!entry.match}
                      checked={chosen.has(entry.index)}
                      onChange={(e) => {
                        const next = new Set(chosen)
                        if (e.target.checked) next.add(entry.index)
                        else next.delete(entry.index)
                        setChosen(next)
                      }}
                    />
                    <img src={entry.image} alt="" className="size-8 rounded bg-[#17181c] object-contain" />
                    <span className="min-w-0 flex-1 truncate text-xs">
                      {entry.artist_name}
                      <span className="block text-muted">
                        {entry.match
                          ? `→ ${entry.match.title} (by ${entry.matched_by === 'mbid' ? 'MusicBrainz ID' : 'name'})`
                          : 'Not in your library'}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
              <Button
                variant="primary"
                disabled={chosen.size === 0}
                busy={applyPack.isPending}
                onClick={() => {
                  const items = preview.entries
                    .filter((x) => x.match && chosen.has(x.index))
                    .map((x) => ({ index: x.index, rating_key: x.match!.rating_key }))
                  applyPack.mutate({ importId: preview.import_id, items }, { onSuccess: () => upload.reset() })
                }}
              >
                Apply {chosen.size}
              </Button>
              {applyPack.isError && <Notice tone="error">{errorMessage(applyPack.error)}</Notice>}
            </div>
          )}
          {latestImport?.status === 'failed' && latestImport.stop_requested && !preview && (
            <p className="text-xs text-muted">Last import: {latestImport.error}</p>
          )}
          {latestImport?.status === 'succeeded' && !preview && (
            <p className="text-xs text-muted">
              Last import: {String((latestImport.stats as Record<string, number>).applied ?? 0)} applied.
            </p>
          )}
        </div>
      </div>
    </Card>
  )
}

function active(j?: Schemas['ArtworkJobOut']) {
  return Boolean(j && (j.status === 'queued' || j.status === 'running'))
}
