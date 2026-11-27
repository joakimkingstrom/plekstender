import { useId, useState } from 'react'
import { errorMessage } from '../api/client'
import {
  LASTFM_CONNECT,
  useAddScrobbler,
  useLastFmScrobbling,
  useRemoveScrobbler,
  useScrobblers,
  type ScrobbleService,
  type ScrobbleTarget,
} from '../api/scrobblers'
import { formatCount, formatRelative } from '../format'
import { Select } from './Select'
import { Button, Card, Notice, Spinner } from './ui'

type Choice = ScrobbleService | ''

const SERVICES: { value: ScrobbleService; label: string; group: string; url?: string; token?: string }[] = [
  {
    value: 'maloja',
    label: 'Maloja',
    group: 'ListenBrainz-compatible',
    url: 'https://maloja.example.com/apis/listenbrainz',
    token: 'One of your Maloja API keys (Settings → API keys).',
  },
  {
    value: 'koito',
    label: 'Koito',
    group: 'ListenBrainz-compatible',
    url: 'https://koito.example.com/apis/listenbrainz',
    token: 'An API key from Koito (Settings → API Keys).',
  },
  {
    value: 'multi-scrobbler',
    label: 'Multi-Scrobbler',
    group: 'ListenBrainz-compatible',
    url: 'http://multi-scrobbler.example.com:9078',
    token: 'The token of its ListenBrainz endpoint source (LZE_TOKEN).',
  },
  {
    value: 'listenbrainz-other',
    label: 'Another ListenBrainz-compatible server',
    group: 'ListenBrainz-compatible',
    url: 'https://scrobbler.example.com',
    token: 'The token the server gives you for ListenBrainz-style scrobbling.',
  },
  { value: 'lastfm', label: 'Last.fm', group: 'Last.fm-style' },
  { value: 'librefm', label: 'Libre.fm', group: 'Last.fm-style' },
]
const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm'
const linkButton = 'inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors'

/** Other places your new Plex plays are scrobbled to: listed compactly, added from a list. */
export function ScrobbleServicesCard() {
  const targets = useScrobblers()
  const [choice, setChoice] = useState<Choice>('')
  const [again, setAgain] = useState<ScrobbleTarget | null>(null)
  const adding = (again?.service ?? choice) as Choice
  return (
    <Card title="More scrobble services">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Send your new Plex plays to other services too, like Last.fm, Maloja, Koito, Multi-Scrobbler or Libre.fm.
        </p>
        {targets.isPending ? (
          <Spinner small />
        ) : targets.isError ? (
          <Notice tone="error">{errorMessage(targets.error)}</Notice>
        ) : (
          targets.data.length > 0 && (
            <ul className="divide-y divide-border rounded-lg border border-border" aria-label="Scrobble services">
              {targets.data.map((t) => (
                <TargetRow key={t.id} target={t} onAgain={() => setAgain(t)} />
              ))}
            </ul>
          )
        )}
        {adding === 'lastfm' ? (
          <LastFmConnect
            onDone={() => {
              setChoice('')
              setAgain(null)
            }}
          />
        ) : adding ? (
          <AddForm
            key={`${adding}-${again?.id ?? ''}`}
            service={adding}
            url={again?.url}
            onDone={() => {
              setChoice('')
              setAgain(null)
            }}
          />
        ) : (
          <Select<Choice>
            label="Add a scrobble service"
            value=""
            options={[{ value: '', label: 'Add a scrobble service…' }, ...SERVICES.map((s) => ({ value: s.value, label: s.label, group: s.group }))]}
            onChange={setChoice}
          />
        )}
      </div>
    </Card>
  )
}

function TargetRow({ target, onAgain }: { target: ScrobbleTarget; onAgain: () => void }) {
  const remove = useRemoveScrobbler()
  const host = (() => {
    try {
      return new URL(target.url).host
    } catch {
      return target.url
    }
  })()
  return (
    <li className="flex items-start gap-3 px-3 py-2">
      <div className="min-w-0 flex-1">
        <p className="font-medium">
          {target.label}
          <span className="font-normal text-muted"> · {target.username ?? host}</span>
        </p>
        <p className={`text-xs ${target.paused || target.error ? 'text-danger' : 'text-muted'}`}>
          {target.paused || target.error
            ? target.error
            : `${formatCount(target.sent)} sent${target.pending ? ` · ${formatCount(target.pending)} waiting` : ''}${
                target.last_submitted_at ? ` · last ${formatRelative(target.last_submitted_at)}` : ''
              }`}
        </p>
      </div>
      {target.paused &&
        (target.service === 'lastfm' ? (
          <a href={LASTFM_CONNECT} className={`${linkButton} bg-surface-2 text-text hover:bg-border`}>
            Connect again
          </a>
        ) : (
          <Button variant="secondary" onClick={onAgain}>
            {target.service === 'librefm' ? 'Sign in again' : 'New token'}
          </Button>
        ))}
      <Button variant="ghost" busy={remove.isPending} onClick={() => remove.mutate(target.id)} aria-label={`Remove ${target.label}`}>
        Remove
      </Button>
    </li>
  )
}

/**
 * Last.fm: approved on last.fm itself (Plekstender never sees the password), which sends
 * the person back to Settings → Scrobbling.
 */
function LastFmConnect({ onDone }: { onDone: () => void }) {
  const lastfm = useLastFmScrobbling()
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <p className="font-medium">Last.fm</p>
      {lastfm.isPending ? (
        <Spinner small />
      ) : lastfm.data?.available ? (
        <p className="text-muted">
          You’ll go to last.fm to let Plekstender scrobble for you, then come back here. Your Last.fm password isn’t
          shared with Plekstender. Plays from then on are sent (like ListenBrainz above).
        </p>
      ) : (
        <p className="text-muted">
          Not set up on this server yet: an admin needs to add a Last.fm API key and its shared secret (Settings →
          Scrobbling).
        </p>
      )}
      <div className="flex gap-2">
        {lastfm.data?.available && (
          <a href={LASTFM_CONNECT} className={`${linkButton} bg-accent text-on-accent hover:bg-accent-strong`}>
            Connect Last.fm
          </a>
        )}
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

/** A web address's host name ('' when it isn't one). */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return ''
  }
}

function AddForm({ service, url: knownUrl, onDone }: { service: ScrobbleService; url?: string; onDone: () => void }) {
  const add = useAddScrobbler()
  const info = SERVICES.find((s) => s.value === service)!
  const libre = service === 'librefm'
  const hintId = useId()
  // Libre.fm's own address isn't shown (it's the default); another GNU FM server's is.
  const [url, setUrl] = useState(knownUrl && !(libre && hostOf(knownUrl) === 'libre.fm') ? knownUrl : '')
  const [token, setToken] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const valid = libre ? Boolean(username.trim() && password) : /^https?:\/\/\S+/.test(url.trim()) && Boolean(token.trim())
  return (
    <form
      className="space-y-2 rounded-lg border border-border p-3"
      onSubmit={(e) => {
        e.preventDefault()
        if (!valid) return
        add.mutate(
          libre
            ? { service, username: username.trim(), password, url: url.trim() || null }
            : { service, url: url.trim(), token: token.trim() },
          { onSuccess: onDone },
        )
      }}
    >
      <p className="font-medium">{info.label}</p>
      {libre ? (
        <>
          <label className="block space-y-1">
            <span className="block">Username</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" className={field} />
          </label>
          <div className="space-y-1">
            <label className="block space-y-1">
              <span className="block">Password</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="off"
                aria-describedby={`${hintId}-password`}
                className={field}
              />
            </label>
            <p id={`${hintId}-password`} className="text-xs text-muted">
              Used once to sign in; only the session it gives is kept.
            </p>
          </div>
          <label className="block space-y-1">
            <span className="block">Server (if not libre.fm)</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://libre.fm/2.0/" className={field} />
          </label>
        </>
      ) : (
        <>
          <label className="block space-y-1">
            <span className="block">Address</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={info.url} className={field} />
          </label>
          <div className="space-y-1">
            <label className="block space-y-1">
              <span className="block">Token</span>
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                autoComplete="off"
                aria-describedby={`${hintId}-token`}
                className={`${field} font-mono`}
              />
            </label>
            <p id={`${hintId}-token`} className="text-xs text-muted">
              {info.token}
            </p>
          </div>
        </>
      )}
      <p className="text-xs text-muted">Plays from now on are sent (like ListenBrainz above).</p>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={!valid} busy={add.isPending}>
          {libre ? 'Sign in and add' : 'Check and add'}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
      {add.isError && <Notice tone="error">{errorMessage(add.error)}</Notice>}
    </form>
  )
}
