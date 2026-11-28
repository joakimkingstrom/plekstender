import { useState, type FormEvent } from 'react'
import { errorMessage } from '../api/client'
import { DEFAULT_TEMPLATE, useApplyRenames, usePlanRenames, useRenamePlan, type RenamePlan } from '../api/files'
import { formatCount } from '../format'
import { storedTemplate, storeTemplate } from '../renameTemplate'
import { Dialog } from './Dialog'
import { Button, Notice, Spinner } from './ui'

const FIELDS = ['albumartist', 'artist', 'album', 'year', 'title', 'track', 'disc', 'genre']

/**
 * Renaming an album's or an artist's files from a template: a dry run lists every move
 * (and what goes along, and what can't move), then the moves are made as one edit, which
 * undo moves back.
 */
export function RenameFiles({
  serverId,
  scope,
  scopeKey,
  canWrite,
  onClose,
}: {
  serverId: number
  scope: 'album' | 'artist'
  scopeKey: number
  canWrite: boolean
  onClose: () => void
}) {
  const [template, setTemplate] = useState(storedTemplate)
  const [jobId, setJobId] = useState<number | null>(null)
  const start = usePlanRenames(serverId)
  const plan = useRenamePlan(serverId, jobId)
  const apply = useApplyRenames(serverId)
  const status = plan.data?.job.status
  const working = start.isPending || status === 'queued' || status === 'running'

  const check = (e: FormEvent) => {
    e.preventDefault()
    storeTemplate(template.trim())
    start.mutate({ scope, key: scopeKey, template: template.trim() }, { onSuccess: (job) => setJobId(job.id) })
  }

  return (
    <Dialog
      title={scope === 'album' ? "Rename this album's files" : "Rename this artist's files"}
      subtitle="Into folders and names from a template. Nothing moves until you've seen where everything goes."
      onClose={onClose}
    >
      <div className="space-y-4 text-sm">
        <form onSubmit={check} className="space-y-2">
          <label className="block">
            <span className="mb-1 block font-medium">Template</span>
            <input
              value={template}
              onChange={(e) => {
                setTemplate(e.target.value)
                setJobId(null)
              }}
              spellCheck={false}
              autoComplete="off"
              className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm"
            />
          </label>
          <div className="space-y-1 text-xs text-muted">
            <p>
              Fields:{' '}
              {FIELDS.map((f) => (
                <code key={f} className="mr-1 rounded bg-surface-2 px-1 py-0.5">{`{${f}}`}</code>
              ))}
            </p>
            <p>
              <code>/</code> makes folders. <code>{'{track:02}'}</code> writes 01, 02… Parts in <code>[ ]</code> are left
              out when a field in them is empty, and <code>{'{disc}'}</code> is empty for one-disc albums. The file's
              extension is kept.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="secondary" busy={working} disabled={!template.trim()}>
              Check where everything goes
            </Button>
            {template !== DEFAULT_TEMPLATE && (
              <Button
                variant="ghost"
                onClick={() => {
                  setTemplate(DEFAULT_TEMPLATE)
                  setJobId(null)
                }}
              >
                Plex's layout
              </Button>
            )}
          </div>
        </form>
        {start.isError && <Notice tone="error">{errorMessage(start.error)}</Notice>}
        {plan.isError && <Notice tone="error">{errorMessage(plan.error)}</Notice>}
        {status === 'failed' && <Notice tone="error">{plan.data?.job.error}</Notice>}
        {working && jobId !== null && (
          <p className="flex items-center gap-2 text-muted">
            <Spinner small /> Looking at the folders…
          </p>
        )}
        {status === 'succeeded' && plan.data && (
          <PlanView
            plan={plan.data}
            canWrite={canWrite}
            busy={apply.isPending}
            onApply={() => apply.mutate(jobId!, { onSuccess: onClose })}
          />
        )}
        {apply.isError && <Notice tone="error">{errorMessage(apply.error)}</Notice>}
      </div>
    </Dialog>
  )
}

function folderOf(path: string) {
  const i = path.lastIndexOf('/')
  return i < 0 ? '' : path.slice(0, i)
}

function nameOf(path: string) {
  return path.slice(path.lastIndexOf('/') + 1)
}

function PlanView({
  plan,
  canWrite,
  busy,
  onApply,
}: {
  plan: RenamePlan
  canWrite: boolean
  busy: boolean
  onApply: () => void
}) {
  const songs = plan.moves.filter((m) => m.kind === 'song').length
  const others = plan.moves.length - songs
  const byFolder = new Map<string, RenamePlan['moves']>()
  const sorted = [...plan.moves].sort((a, b) => a.target.localeCompare(b.target, undefined, { numeric: true }))
  for (const m of sorted) byFolder.set(folderOf(m.target), [...(byFolder.get(folderOf(m.target)) ?? []), m])

  return (
    <div className="space-y-3">
      <p>
        {songs ? (
          <>
            <strong>
              {formatCount(songs)} {songs === 1 ? 'song moves' : 'songs move'}
            </strong>
            {others > 0 && `, and ${formatCount(others)} other ${others === 1 ? 'file goes' : 'files go'} along`}.
          </>
        ) : (
          'No songs move.'
        )}{' '}
        {plan.unchanged > 0 && `${formatCount(plan.unchanged)} ${plan.unchanged === 1 ? 'is' : 'are'} already in place.`}
      </p>

      {byFolder.size > 0 && (
        <div className="max-h-[45dvh] space-y-3 overflow-y-auto rounded-lg border border-border p-3">
          {[...byFolder.entries()].map(([folder, moves]) => (
            <section key={folder}>
              <h4 className="break-all font-mono text-xs font-semibold">{folder || '(the music folder)'}/</h4>
              <ul className="mt-1 space-y-1">
                {moves.map((m) => (
                  <li key={m.source} className="pl-3">
                    <span className="block break-all">
                      {nameOf(m.target)}
                      {m.kind !== 'song' && <span className="text-xs text-muted"> (goes along)</span>}
                    </span>
                    <span className="block break-all text-xs text-muted">from {m.source}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      {plan.skipped.length > 0 && (
        <div className="space-y-1">
          <p className="font-medium text-warn">
            {plan.skipped.length} {plan.skipped.length === 1 ? 'file stays' : 'files stay'} where{' '}
            {plan.skipped.length === 1 ? 'it is' : 'they are'}:
          </p>
          <ul className="space-y-1 text-xs">
            {plan.skipped.map((s) => (
              <li key={s.path}>
                <span className="break-all font-mono">{s.path}</span>
                <span className="text-muted">
                  {' '}
                  · {s.reason}
                  {s.target && s.target !== s.path ? ` (${s.target})` : ''}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {plan.moves.length > 0 &&
        (canWrite ? (
          <div className="space-y-2 border-t border-border pt-3">
            <p className="text-xs text-muted">
              Nothing is overwritten, and emptied folders are removed. Plex is asked to rescan the library, which
              usually recognises moved files and keeps their ratings and plays: try one album first. Undo moves
              everything back.
            </p>
            <div className="flex justify-end">
              <Button variant="primary" busy={busy} onClick={onApply}>
                Move {formatCount(plan.moves.length)} {plan.moves.length === 1 ? 'file' : 'files'}
              </Button>
            </div>
          </div>
        ) : (
          <Notice>Turn on "Allow changing files" (Settings → Music files → Folder scan) to move files.</Notice>
        ))}
    </div>
  )
}
