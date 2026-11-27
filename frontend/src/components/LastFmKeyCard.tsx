import { useId, useState } from 'react'
import { errorMessage } from '../api/client'
import { useLastFmKey, useSetLastFmKey } from '../api/listens'
import { Button, Card, Notice, Spinner } from './ui'

const HEX32 = /^[0-9A-Fa-f]{32}$/
const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm'

/**
 * The instance's Last.fm API key and its shared secret (admins). The key alone is enough for
 * importing scrobbles; scrobbling to Last.fm needs the secret too (Last.fm signs those calls).
 * Since 1 December 2026 importing needs a key whose owner has Last.fm Pro; scrobbling doesn't.
 */
export function LastFmKeyCard() {
  const key = useLastFmKey(true)
  const save = useSetLastFmKey()
  const id = useId()
  const [apiKey, setApiKey] = useState('')
  const [secret, setSecret] = useState('')
  const [saved, setSaved] = useState<string | null>(null)
  const configured = Boolean(key.data?.configured)
  const keyOk = !apiKey.trim() || HEX32.test(apiKey.trim())
  const secretOk = !secret.trim() || HEX32.test(secret.trim())
  // A secret on its own goes with the key that's there.
  const valid = keyOk && secretOk && (Boolean(apiKey.trim()) || (configured && Boolean(secret.trim())))
  const clearing = save.isPending && save.variables?.api_key == null && save.variables?.shared_secret == null

  return (
    <Card title="Last.fm API key">
      <div className="space-y-4">
        <p className="text-sm text-muted">
          For everyone’s Last.fm imports and scrobbling to Last.fm on this server. Create an API account at{' '}
          <a href="https://www.last.fm/api/account/create" target="_blank" rel="noreferrer" className="text-accent underline">
            last.fm/api/account/create
          </a>{' '}
          (the callback URL can be left empty); it shows the API key and the shared secret. Scrobbling works with any key;
          from 1 December 2026, importing only works with a key whose owner has Last.fm Pro.
        </p>
        {key.isPending ? (
          <Spinner small />
        ) : key.isError ? (
          <Notice tone="error">{errorMessage(key.error)}</Notice>
        ) : (
          <ul className="space-y-1 text-sm">
            <li>
              {key.data.source === 'settings'
                ? '✓ A key is set on this page.'
                : key.data.source === 'environment'
                  ? '✓ Using the key from the LASTFM_API_KEY environment variable. A key saved here takes precedence.'
                  : 'No key is set, so Last.fm import and scrobbling are off.'}
            </li>
            {key.data.configured && (
              <li className={key.data.scrobbling ? '' : 'text-muted'}>
                {key.data.scrobbling
                  ? '✓ The shared secret is set: people can scrobble to Last.fm.'
                  : 'Add the shared secret to let people scrobble to Last.fm.'}
              </li>
            )}
          </ul>
        )}
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (!valid) return
            save.mutate(
              { api_key: apiKey.trim() || null, shared_secret: secret.trim() || null },
              {
                onSuccess: (out) => {
                  setSaved(
                    out.note ??
                      (apiKey.trim() && secret.trim() ? 'Saved. Last.fm accepted the key and secret.' : apiKey.trim() ? 'Saved. Last.fm accepted the key.' : 'Saved. Last.fm accepted the secret.'),
                  )
                  setApiKey('')
                  setSecret('')
                },
              },
            )
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-sm font-medium">{configured ? 'Replace key' : 'API key'}</span>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => {
                  setSaved(null)
                  setApiKey(e.target.value)
                }}
                autoComplete="off"
                spellCheck={false}
                placeholder="32 characters"
                className={field}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium">{key.data?.scrobbling ? 'Replace shared secret' : 'Shared secret'}</span>
              <input
                type="password"
                value={secret}
                onChange={(e) => {
                  setSaved(null)
                  setSecret(e.target.value)
                }}
                autoComplete="off"
                spellCheck={false}
                placeholder="32 characters"
                aria-describedby={`${id}-secret`}
                className={field}
              />
            </label>
          </div>
          <p id={`${id}-secret`} className="text-xs text-muted">
            The secret is only needed for scrobbling. A new key without its secret turns scrobbling off until the secret
            is added too.
          </p>
          {(!keyOk || !secretOk) && (
            <p className="text-xs text-danger">The key and the secret are each 32 letters and digits (0–9, a–f).</p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending && !clearing}>
              Check and save
            </Button>
            {key.data?.source === 'settings' && (
              <Button
                variant="ghost"
                busy={clearing}
                onClick={() => {
                  setSaved(null)
                  save.mutate({})
                }}
              >
                Remove key and secret
              </Button>
            )}
          </div>
        </form>
        {saved && <Notice>{saved}</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
