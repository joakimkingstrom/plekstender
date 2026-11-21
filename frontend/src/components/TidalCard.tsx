import { useState } from 'react'
import { useSetTidal, useTidal } from '../api/albumCovers'
import { errorMessage } from '../api/client'
import { Button, Card, Notice, Spinner } from './ui'

const field = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm'

/** Tidal's catalogue for album covers and playlist links (admins): a free app's client ID and secret. */
export function TidalCard() {
  const tidal = useTidal(true)
  const save = useSetTidal()
  const [id, setId] = useState('')
  const [secret, setSecret] = useState('')
  const [saved, setSaved] = useState(false)
  const valid = /^[A-Za-z0-9]{8,64}$/.test(id.trim()) && secret.trim().length >= 8
  return (
    <Card title="Tidal">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Album covers from Tidal’s catalogue (Library → Artwork → Album covers) and importing public Tidal playlists from
          their links (Playlists). The same app is used for both, so it shows under Services → Playlists and Artwork. Make
          a free app at{' '}
          <a href="https://developer.tidal.com" target="_blank" rel="noreferrer" className="text-accent underline">
            developer.tidal.com
          </a>{' '}
          and paste its client ID and secret. No Tidal subscription is needed.
        </p>
        {tidal.isPending ? (
          <Spinner small />
        ) : tidal.isError ? (
          <Notice tone="error">{errorMessage(tidal.error)}</Notice>
        ) : (
          <p>{tidal.data.configured ? `✓ Set up (client ${tidal.data.client_id}).` : 'Not set up.'}</p>
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
              <span className="mb-1 block font-medium">Client ID</span>
              <input value={id} onChange={(e) => (setSaved(false), setId(e.target.value))} autoComplete="off" spellCheck={false} className={field} />
            </label>
            <label className="block">
              <span className="mb-1 block font-medium">Client secret</span>
              <input
                type="password"
                value={secret}
                onChange={(e) => (setSaved(false), setSecret(e.target.value))}
                autoComplete="off"
                spellCheck={false}
                className={field}
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending && save.variables?.client_id != null}>
              Check and save
            </Button>
            {tidal.data?.configured && (
              <Button variant="ghost" busy={save.isPending && save.variables?.client_id == null} onClick={() => (setSaved(false), save.mutate({}))}>
                Remove
              </Button>
            )}
          </div>
        </form>
        {saved && <Notice>Saved. Tidal accepted them.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
