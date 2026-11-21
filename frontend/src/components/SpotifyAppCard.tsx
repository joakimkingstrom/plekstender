import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useSetSpotifyApp, useSpotifyApp } from '../api/playlists'
import { Button, Card, Notice, Spinner } from './ui'

/** The Spotify app (admins) used to read public playlist links for imports. */
export function SpotifyAppCard() {
  const app = useSpotifyApp(true)
  const save = useSetSpotifyApp()
  const [id, setId] = useState('')
  const [secret, setSecret] = useState('')
  const [saved, setSaved] = useState(false)
  const valid = /^[A-Za-z0-9]{20,64}$/.test(id.trim()) && /^[A-Za-z0-9]{20,64}$/.test(secret.trim())
  const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm'
  return (
    <Card title="Spotify app">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          Lets people import public Spotify playlists by their link (Playlists → Import a playlist). Create an app at{' '}
          <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noreferrer" className="text-accent underline">
            developer.spotify.com/dashboard
          </a>{' '}
          (Web API) and paste its client ID and secret. Spotify’s own editorial and algorithmic playlists can’t be read.
        </p>
        {app.isPending ? (
          <Spinner small />
        ) : app.isError ? (
          <Notice tone="error">{errorMessage(app.error)}</Notice>
        ) : (
          <p className="text-sm">
            {app.data.source === 'settings'
              ? `App ${app.data.client_id} is set on this page.`
              : app.data.source === 'environment'
                ? 'Using SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET from the environment. An app saved here takes precedence.'
                : 'No app is set.'}
          </p>
        )}
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid)
              save.mutate(
                { client_id: id.trim(), client_secret: secret.trim() },
                {
                  onSuccess: () => {
                    setId('')
                    setSecret('')
                    setSaved(true)
                  },
                },
              )
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Client ID</span>
              <input value={id} autoComplete="off" spellCheck={false} onChange={(e) => setId(e.target.value)} className={field} />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Client secret</span>
              <input type="password" value={secret} autoComplete="off" spellCheck={false} onChange={(e) => setSecret(e.target.value)} className={field} />
            </label>
          </div>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending && Boolean(save.variables?.client_id)}>
              Check and save
            </Button>
            {app.data?.source === 'settings' && (
              <Button
                variant="ghost"
                busy={save.isPending && !save.variables?.client_id}
                onClick={() => save.mutate({ client_id: null, client_secret: null })}
              >
                Remove
              </Button>
            )}
          </div>
        </form>
        {saved && <Notice>Saved. Spotify accepted the app.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
