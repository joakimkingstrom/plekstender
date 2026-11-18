import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useMe } from '../api/hooks'
import {
  useDeleteListens,
  useLastFmKey,
  useLastFmStatus,
  useResumeImport,
  useSetLastFmKey,
  useStartLastFmImport,
  type ListenSource,
} from '../api/listens'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { formatCount, formatRelative } from '../format'

/** From 1 Dec 2026 Last.fm API keys only work for Last.fm Pro subscribers. */
const BEFORE_LASTFM_PRO = Date.now() < Date.UTC(2026, 11, 1)

const inputClass = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm'

export function ListeningSettingsPage() {
  const me = useMe()
  if (me.isPending) return <Spinner />
  const isAdmin = Boolean(me.data?.is_admin)
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <LastFmImportCard isAdmin={isAdmin} />
        <DeleteHistoryCard />
      </div>
      {isAdmin && (
        <div>
          <LastFmKeyCard />
        </div>
      )}
    </div>
  )
}

function ImportProgress({ job }: { job: Schemas['ListenImportOut'] }) {
  const { done = 0, total = 0 } = job.progress as { done?: number; total?: number }
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : null
  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex justify-between gap-3 text-sm">
        <span>
          {job.status === 'queued' ? 'Waiting for the background worker…' : `Importing ${job.username}'s scrobbles…`}
        </span>
        {total > 0 && (
          <span className="text-muted tabular-nums">
            {formatCount(done)} / {formatCount(total)}
          </span>
        )}
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div
          className={`h-full rounded-full bg-accent transition-[width] ${pct === null ? 'w-1/3 animate-pulse' : ''}`}
          style={pct === null ? undefined : { width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function LastImport({ job }: { job: Schemas['ListenImportOut'] }) {
  const resume = useResumeImport()
  const added = Number(job.stats.added ?? 0)
  const read = Number((job.progress as { done?: number }).done ?? 0)
  const when = job.finished_at ? formatRelative(job.finished_at) : ''
  if (job.status === 'failed') {
    return (
      <div className="space-y-3">
        <Notice tone="error">
          The import of {job.username}'s scrobbles stopped {when}: {job.error ?? 'unknown error'}
        </Notice>
        <p className="text-sm text-muted">
          {formatCount(added)} listens were saved before it stopped. Resuming continues where it left off.
        </p>
        <Button busy={resume.isPending} onClick={() => resume.mutate(job.id)}>
          Resume import
        </Button>
        {resume.isError && <Notice tone="error">{errorMessage(resume.error)}</Notice>}
      </div>
    )
  }
  return (
    <p className="text-sm text-muted">
      Imported {when} from {job.username}: {formatCount(added)} new{' '}
      {added === 1 ? 'listen' : 'listens'}
      {read > added && ` (${formatCount(read - added)} were already here)`}.{' '}
      <Link to="/history" className="text-accent underline">
        View history
      </Link>
    </p>
  )
}

function LastFmImportCard({ isAdmin }: { isAdmin: boolean }) {
  const status = useLastFmStatus()
  const start = useStartLastFmImport()
  const [username, setUsername] = useState<string | null>(null)

  if (status.isPending) return <Card title="Import from Last.fm"><Spinner small /></Card>
  if (status.isError) return <Card title="Import from Last.fm"><Notice tone="error">{errorMessage(status.error)}</Notice></Card>
  const { available, active, last } = status.data
  const name = username ?? status.data.username ?? ''
  const valid = /^[A-Za-z][A-Za-z0-9_-]{1,14}$/.test(name.trim())
  const repeat = last?.status === 'succeeded' && last.username.toLowerCase() === name.trim().toLowerCase()

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (valid) start.mutate(name.trim())
  }

  return (
    <Card title="Import from Last.fm">
      <div className="space-y-4">
        <p className="text-sm text-muted">
          Copies your scrobbles into your listening history here. Nothing is changed on Last.fm or in
          Plex. Running it again only fetches scrobbles added since the last import.
        </p>
        {BEFORE_LASTFM_PRO && (
          <Notice tone="warn">
            From 1 December 2026, Last.fm only lets apps read scrobbles if the app's API key belongs to
            a Last.fm Pro subscriber. Import before then to be safe.
          </Notice>
        )}
        {!available ? (
          <Notice>
            Last.fm import isn't set up on this instance yet.{' '}
            {isAdmin ? 'Add a Last.fm API key in the card next to this one.' : 'Ask an admin to add a Last.fm API key.'}
          </Notice>
        ) : active ? (
          <ImportProgress job={active} />
        ) : (
          <>
            {last && <LastImport job={last} />}
            <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
              <label className="min-w-0 flex-1">
                <span className="mb-1 block text-sm font-medium">Last.fm username</span>
                <input
                  value={name}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  maxLength={15}
                  className={inputClass}
                />
              </label>
              <Button type="submit" variant="primary" disabled={!valid} busy={start.isPending}>
                {repeat ? 'Import new scrobbles' : 'Import'}
              </Button>
            </form>
            <p className="text-xs text-muted">
              Your profile's recent listening must be public (Last.fm → Settings → Privacy).
            </p>
          </>
        )}
        {start.isError && <Notice tone="error">{errorMessage(start.error)}</Notice>}
      </div>
    </Card>
  )
}

function ConfirmDelete({ label, source }: { label: string; source: ListenSource | null }) {
  const remove = useDeleteListens()
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<number | null>(null)
  return (
    <div className="space-y-2">
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm">This can't be undone.</span>
          <Button
            variant="danger"
            busy={remove.isPending}
            onClick={() =>
              remove.mutate(source, {
                onSuccess: (data) => {
                  setResult(data.deleted)
                  setConfirming(false)
                },
              })
            }
          >
            Yes, delete
          </Button>
          <Button variant="ghost" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <Button variant="danger" onClick={() => { setResult(null); setConfirming(true) }}>
          {label}
        </Button>
      )}
      {result !== null && <p className="text-sm text-muted">Deleted {formatCount(result)} listens.</p>}
      {remove.isError && <Notice tone="error">{errorMessage(remove.error)}</Notice>}
    </div>
  )
}

function DeleteHistoryCard() {
  return (
    <Card title="Delete history">
      <p className="mb-4 text-sm text-muted">
        Removes listens from Plekstender only. Last.fm and Plex keep their own copies.
      </p>
      <div className="flex flex-wrap gap-4">
        <ConfirmDelete label="Delete imported Last.fm listens" source="lastfm" />
        <ConfirmDelete label="Delete all listening history" source={null} />
      </div>
    </Card>
  )
}

function LastFmKeyCard() {
  const key = useLastFmKey(true)
  const save = useSetLastFmKey()
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)
  const valid = /^[0-9A-Fa-f]{32}$/.test(value.trim())

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!valid) return
    save.mutate(value.trim(), {
      onSuccess: () => {
        setValue('')
        setSaved(true)
      },
    })
  }

  return (
    <Card title="Last.fm API key">
      <div className="space-y-4">
        <p className="text-sm text-muted">
          Used for everyone's Last.fm imports on this instance. Create one at{' '}
          <a href="https://www.last.fm/api/account/create" target="_blank" rel="noreferrer" className="text-accent underline">
            last.fm/api/account/create
          </a>
          . From 1 December 2026 it only works while its owner has Last.fm Pro.
        </p>
        {key.isPending ? (
          <Spinner small />
        ) : key.isError ? (
          <Notice tone="error">{errorMessage(key.error)}</Notice>
        ) : (
          <p className="text-sm">
            {key.data.source === 'settings'
              ? 'A key is set on this page.'
              : key.data.source === 'environment'
                ? 'Using the key from the LASTFM_API_KEY environment variable. A key saved here takes precedence.'
                : 'No key is set.'}
          </p>
        )}
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <label className="min-w-0 flex-1">
            <span className="mb-1 block text-sm font-medium">{key.data?.configured ? 'Replace key' : 'API key'}</span>
            <input
              type="password"
              value={value}
              onChange={(e) => {
                setSaved(false)
                setValue(e.target.value)
              }}
              autoComplete="off"
              spellCheck={false}
              placeholder="32 characters"
              className={`${inputClass} font-mono`}
            />
          </label>
          <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending && save.variables !== null}>
            Check and save
          </Button>
        </form>
        {value && !valid && <p className="text-xs text-danger">A Last.fm API key is 32 letters and digits (0–9, a–f).</p>}
        {key.data?.source === 'settings' && (
          <Button
            variant="ghost"
            busy={save.isPending && save.variables === null}
            onClick={() => {
              setSaved(false)
              save.mutate(null)
            }}
          >
            Remove key
          </Button>
        )}
        {saved && <Notice>Saved. Last.fm accepted the key.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
