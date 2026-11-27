import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useCoverSongsSettings, useSetCoverSongs } from '../api/coverSongs'
import { formatCount } from '../format'
import { Button, Card, Notice, Spinner } from './ui'

const SHS = 'https://secondhandsongs.com'

/** Cover songs from SecondHandSongs (admins): turn the lookups on, optionally with a key. */
export function CoverSongsCard() {
  const current = useCoverSongsSettings()
  const save = useSetCoverSongs()
  const [key, setKey] = useState('')
  const [saved, setSaved] = useState(false)
  const validKey = /^[A-Za-z0-9-]{20,64}$/.test(key.trim())
  if (current.isPending) return <Spinner />
  if (current.isError) return <Notice tone="error">{errorMessage(current.error)}</Notice>
  const c = current.data
  const hours = c.enabled && c.songs > c.checked ? Math.ceil((c.songs - c.checked) / Math.max(1, c.per_hour * 0.7)) : 0
  return (
    <Card title="SecondHandSongs">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Finds out which songs in the libraries are covers, and whose song they are, from{' '}
          <a href={SHS} target="_blank" rel="noreferrer" className="text-accent underline">
            SecondHandSongs
          </a>
          . Song rows then say “Cover of …”. The worker looks songs up a few at a time within SecondHandSongs’ rate
          limits: the songs people play and rate first.
        </p>
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={c.enabled}
            onChange={(e) => save.mutate({ enabled: e.target.checked, remove_key: false })}
          />
          <span>
            <span className="font-medium">Look up cover songs</span>
            <span className="block text-xs text-muted">
              Song titles and artist names are sent to SecondHandSongs. Its terms ask for permission before using its
              data beyond private use.
            </span>
          </span>
        </label>
        {c.songs > 0 && (
          <p className="text-xs text-muted">
            {formatCount(c.checked)} of {formatCount(c.songs)} songs looked up, {formatCount(c.covers)} covers found
            {hours > 0 && `. About ${formatCount(hours)} more hours at ${formatCount(c.per_hour)} requests an hour`}.
          </p>
        )}
        <form
          className="flex flex-wrap items-end gap-2 border-t border-border pt-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (validKey)
              save.mutate(
                { enabled: c.enabled, api_key: key.trim(), remove_key: false },
                {
                  onSuccess: () => {
                    setKey('')
                    setSaved(true)
                  },
                },
              )
          }}
        >
          <label className="min-w-0 flex-1">
            <span className="mb-1 block font-medium">
              API key <span className="font-normal text-muted">(optional: higher limits)</span>
            </span>
            <input
              type="password"
              value={key}
              autoComplete="off"
              spellCheck={false}
              placeholder={c.api_key ? 'A key is saved' : ''}
              onChange={(e) => {
                setSaved(false)
                setKey(e.target.value)
              }}
              className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm"
            />
          </label>
          <Button type="submit" variant="primary" disabled={!validKey} busy={save.isPending && Boolean(save.variables?.api_key)}>
            Check and save
          </Button>
          {c.api_key && (
            <Button
              variant="ghost"
              busy={save.isPending && Boolean(save.variables?.remove_key)}
              onClick={() => save.mutate({ enabled: c.enabled, remove_key: true })}
            >
              Remove
            </Button>
          )}
        </form>
        <p className="text-xs text-muted">
          Request a key from SecondHandSongs (see{' '}
          <a href={`${SHS}/page/API/Access`} target="_blank" rel="noreferrer" className="text-accent underline">
            API access
          </a>
          ).
        </p>
        {saved && <Notice>Saved. SecondHandSongs accepted the key.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
