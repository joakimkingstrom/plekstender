import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useMe, useSetPreferences } from '../api/hooks'
import {
  useConnectListenBrainz,
  useDeleteListens,
  useDisconnectListenBrainz,
  useListenBrainz,
  useListenBrainzImport,
  useListenBrainzSend,
  useLastFmKey,
  useLastFmStatus,
  usePlexCapture,
  useResumeImport,
  useSetLastFmKey,
  useStartLastFmImport,
  useStartListenBrainzImport,
  useStartListenBrainzSend,
  type ListenSource,
} from '../api/listens'
import { Stepper } from '../components/controls'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { formatCount, formatRelative } from '../format'

/** From 1 Dec 2026 Last.fm API keys only work for Last.fm Pro subscribers. */
const BEFORE_LASTFM_PRO = Date.now() < Date.UTC(2026, 11, 1)

const inputClass = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm'

export function ListeningSettingsPage() {
  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <PlexPlaysCard />
      <SkipsCard />
      <DeleteHistoryCard />
    </div>
  )
}

function PlexPlaysCard() {
  const capture = usePlexCapture()
  return (
    <Card title="Plays from Plex">
      <p className="mb-4 text-sm text-muted">
        Plekstender reads your servers' play history every 5 minutes, including plays from before
        you connected. A Last.fm scrobble of the same play counts only once.
      </p>
      {capture.isPending ? (
        <Spinner small />
      ) : capture.isError ? (
        <Notice tone="error">{errorMessage(capture.error)}</Notice>
      ) : capture.data.length === 0 ? (
        <p className="text-sm text-muted">
          <Link to="/settings" className="text-accent underline">
            Connect a server
          </Link>{' '}
          to start.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {capture.data.map((s) => (
            <li key={s.server_id} className="space-y-1 py-2 first:pt-0 last:pb-0">
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="font-medium">{s.server_name}</span>
                <span className="text-muted tabular-nums">
                  {formatCount(s.listens)} {s.listens === 1 ? 'play' : 'plays'}
                </span>
              </div>
              <p className="text-xs text-muted">{describeCapture(s)}</p>
              {s.error && <Notice tone="error">{s.error}</Notice>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function describeCapture(s: Schemas['PlexCaptureOut']): string {
  if (!s.checked_at) return 'Waiting for the first library sync to finish.'
  if (!s.backfilled) return 'Reading past plays…'
  return `Checked ${formatRelative(s.checked_at)}.`
}

function ImportProgress({ job }: { job: Schemas['ListenImportOut'] }) {
  const noun = job.source === 'lastfm' ? 'scrobbles' : 'listens'
  const { done = 0, total = 0 } = job.progress as { done?: number; total?: number }
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : null
  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex justify-between gap-3 text-sm">
        <span>
          {job.status === 'queued' ? 'Waiting for the background worker…' : `Importing ${job.username}'s ${noun}…`}
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
  const noun = job.source === 'lastfm' ? 'scrobbles' : 'listens'
  const resume = useResumeImport()
  const added = Number(job.stats.added ?? 0)
  const read = Number((job.progress as { done?: number }).done ?? 0)
  const when = job.finished_at ? formatRelative(job.finished_at) : ''
  if (job.status === 'failed') {
    return (
      <div className="space-y-3">
        <Notice tone="error">
          The import of {job.username}'s {noun} stopped {when}: {job.error ?? 'unknown error'}
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

export function LastFmImportCard({ isAdmin }: { isAdmin: boolean }) {
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

export function ListenBrainzImportCard() {
  const status = useListenBrainzImport()
  const start = useStartListenBrainzImport()
  const [username, setUsername] = useState<string | null>(null)

  if (status.isPending) return <Card title="Import from ListenBrainz"><Spinner small /></Card>
  if (status.isError) return <Card title="Import from ListenBrainz"><Notice tone="error">{errorMessage(status.error)}</Notice></Card>
  const { active, last } = status.data
  const name = username ?? status.data.username ?? ''
  const valid = /^[^/?#\s][^/?#]{0,63}$/.test(name.trim())
  const repeat = last?.status === 'succeeded' && last.username === name.trim()

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (valid) start.mutate(name.trim())
  }

  return (
    <Card title="Import from ListenBrainz">
      <div className="space-y-4">
        <p className="text-sm text-muted">
          Copies a ListenBrainz user's public listens into your history. Useful after 1 December if
          you've moved your Last.fm history to ListenBrainz with its own importer. Listens you also
          have from Last.fm or Plex count once.
        </p>
        {active ? (
          <ImportProgress job={active} />
        ) : (
          <>
            {last && <LastImport job={last} />}
            <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
              <label className="min-w-0 flex-1">
                <span className="mb-1 block text-sm font-medium">ListenBrainz username</span>
                <input
                  value={name}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  maxLength={64}
                  className={inputClass}
                />
              </label>
              <Button type="submit" variant="primary" disabled={!valid} busy={start.isPending}>
                {repeat ? 'Import new listens' : 'Import'}
              </Button>
            </form>
          </>
        )}
        {start.isError && <Notice tone="error">{errorMessage(start.error)}</Notice>}
      </div>
    </Card>
  )
}

export function ListenBrainzCard() {
  const status = useListenBrainz()
  const connect = useConnectListenBrainz()
  const disconnect = useDisconnectListenBrainz()
  const [token, setToken] = useState('')
  const valid = /^[A-Za-z0-9-]{8,128}$/.test(token.trim())

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (valid) connect.mutate(token.trim(), { onSuccess: () => setToken('') })
  }

  const s = status.data
  const tokenForm = (label: string) => (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <label className="min-w-0 flex-1">
        <span className="mb-1 block text-sm font-medium">{label}</span>
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          className={`${inputClass} font-mono`}
        />
      </label>
      <Button type="submit" variant="primary" disabled={!valid} busy={connect.isPending}>
        Connect
      </Button>
    </form>
  )

  return (
    <Card title="Scrobble to ListenBrainz">
      <div className="space-y-4">
        <p className="text-sm text-muted">
          Sends your new Plex plays to{' '}
          <a href="https://listenbrainz.org" target="_blank" rel="noreferrer" className="text-accent underline">
            ListenBrainz
          </a>{' '}
          a few minutes after they finish. If another app already scrobbles your Plex plays to
          ListenBrainz, leave this off to avoid doubles. Older plays and imported Last.fm scrobbles
          can be sent once connected.
        </p>
        {status.isPending ? (
          <Spinner small />
        ) : status.isError ? (
          <Notice tone="error">{errorMessage(status.error)}</Notice>
        ) : !s?.connected ? (
          <>
            {tokenForm('User token')}
            <p className="text-xs text-muted">
              Find it at{' '}
              <a href="https://listenbrainz.org/settings/" target="_blank" rel="noreferrer" className="text-accent underline">
                listenbrainz.org/settings
              </a>
              .
            </p>
          </>
        ) : (
          <>
            <p className="text-sm">
              Connected as <span className="font-medium">{s.username}</span>. {formatCount(s.sent)}{' '}
              {s.sent === 1 ? 'play' : 'plays'} sent
              {s.last_submitted_at && `, the last ${formatRelative(s.last_submitted_at)}`}
              {s.pending > 0 && `; ${formatCount(s.pending)} waiting`}.
            </p>
            {s.error && <Notice tone={s.paused ? 'error' : 'warn'}>{s.error}</Notice>}
            {s.paused && tokenForm('New user token')}
            {!s.paused && <SendHistory />}
            <Button variant="ghost" busy={disconnect.isPending} onClick={() => disconnect.mutate()}>
              Disconnect
            </Button>
          </>
        )}
        {connect.isError && <Notice tone="error">{errorMessage(connect.error)}</Notice>}
        {disconnect.isError && <Notice tone="error">{errorMessage(disconnect.error)}</Notice>}
      </div>
    </Card>
  )
}

/** Send every listen (Plex plays and imported Last.fm scrobbles) to ListenBrainz. */
function SendHistory() {
  const status = useListenBrainzSend()
  const start = useStartListenBrainzSend()
  const resume = useResumeImport()
  const active = status.data?.active
  const last = status.data?.last
  const stats = (last?.stats ?? {}) as { sent?: number; existing?: number; refused?: number }
  return (
    <div className="space-y-3 border-t border-border pt-4">
      <h3 className="text-sm font-semibold">Your whole history</h3>
      {active ? (
        <SendProgress job={active} />
      ) : (
        <>
          <p className="text-sm text-muted">
            Sends every listen in Plekstender (Plex plays and imported Last.fm scrobbles, each play once). Listens
            already on ListenBrainz, including ones from its own Last.fm import, are left out, so it's safe to run
            again later.
          </p>
          {last?.status === 'succeeded' && (
            <p className="text-sm">
              Last run {last.finished_at && formatRelative(last.finished_at)}: {formatCount(stats.sent ?? 0)} sent,{' '}
              {formatCount(stats.existing ?? 0)} already there
              {stats.refused ? `, ${formatCount(stats.refused)} refused by ListenBrainz` : ''}.
            </p>
          )}
          {last?.status === 'failed' && (
            <Notice tone="error">
              It stopped {last.finished_at && formatRelative(last.finished_at)}: {last.error ?? 'unknown error'}
            </Notice>
          )}
          <div className="flex flex-wrap gap-2">
            {last?.status === 'failed' && (
              <Button busy={resume.isPending} onClick={() => resume.mutate(last.id)}>
                Resume
              </Button>
            )}
            <Button busy={start.isPending} onClick={() => start.mutate()}>
              {last ? 'Send again' : 'Send my whole history'}
            </Button>
          </div>
        </>
      )}
      {start.isError && <Notice tone="error">{errorMessage(start.error)}</Notice>}
      {resume.isError && <Notice tone="error">{errorMessage(resume.error)}</Notice>}
    </div>
  )
}

function SendProgress({ job }: { job: Schemas['ListenImportOut'] }) {
  const { done = 0, total = 0 } = job.progress as { done?: number; total?: number }
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : null
  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex justify-between gap-3 text-sm">
        <span>{job.status === 'queued' ? 'Waiting for the background worker…' : 'Sending your history…'}</span>
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
    <Card title="Your history">
      <div className="space-y-4">
        <p className="text-sm text-muted">
          Deleting removes listens from Plekstender only. Last.fm, ListenBrainz and Plex keep their own
          copies.
        </p>
        <div className="flex flex-wrap gap-4">
          <ConfirmDelete label="Delete imported Last.fm listens" source="lastfm" />
          <ConfirmDelete label="Delete imported ListenBrainz listens" source="listenbrainz" />
          <ConfirmDelete label="Delete all listening history" source={null} />
        </div>
      </div>
    </Card>
  )
}

export function LastFmKeyCard() {
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

function SkipsCard() {
  const me = useMe()
  const save = useSetPreferences()
  const [value, setValue] = useState<number | null>(null)
  if (me.isPending || !me.data) return null
  const saved = me.data.preferences?.skip_threshold ?? 80
  const current = value ?? saved
  const valid = Number.isInteger(current) && current >= 10 && current <= 95
  return (
    <Card title="Skips">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          While something plays in Plex, Plekstender notes how far each song gets. A play counts as skipped when less
          than this much of the song was heard. Used for "Most skipped" on History.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Stepper label="Skip threshold" value={current} min={10} max={95} onChange={setValue} invalid={!valid} />
          <span className="text-sm text-muted">% of the song</span>
          <Button
            variant="primary"
            disabled={!valid || current === saved}
            busy={save.isPending}
            onClick={() =>
              save.mutate({ skip_threshold: current }, { onSuccess: () => setValue(null) })
            }
          >
            Save
          </Button>
        </div>
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
