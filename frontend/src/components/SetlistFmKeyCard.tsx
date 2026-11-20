import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useSetlistFmKey, useSetSetlistFmKey } from '../api/gigs'
import { Button, Card, Notice, Spinner } from './ui'

/** The instance's setlist.fm API key (admins), for importing the gigs people went to. */
export function SetlistFmKeyCard() {
  const key = useSetlistFmKey(true)
  const save = useSetSetlistFmKey()
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)
  const valid = /^[A-Za-z0-9-]{20,64}$/.test(value.trim())
  return (
    <Card title="setlist.fm API key">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          Lets people import the concerts they marked as attended on setlist.fm (History → Gigs). Apply for a free,
          non-commercial key at{' '}
          <a href="https://www.setlist.fm/settings/api" target="_blank" rel="noreferrer" className="text-accent underline">
            setlist.fm/settings/api
          </a>
          .
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
                ? 'Using SETLISTFM_API_KEY from the environment. A key saved here takes precedence.'
                : 'No key is set.'}
          </p>
        )}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid)
              save.mutate(value.trim(), {
                onSuccess: () => {
                  setValue('')
                  setSaved(true)
                },
              })
          }}
        >
          <label className="min-w-0 flex-1">
            <span className="mb-1 block text-sm font-medium">{key.data?.configured ? 'Replace key' : 'API key'}</span>
            <input
              type="password"
              value={value}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => {
                setSaved(false)
                setValue(e.target.value)
              }}
              className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm"
            />
          </label>
          <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending && save.variables !== null}>
            Check and save
          </Button>
          {key.data?.source === 'settings' && (
            <Button variant="ghost" busy={save.isPending && save.variables === null} onClick={() => save.mutate(null)}>
              Remove
            </Button>
          )}
        </form>
        {saved && <Notice>Saved. setlist.fm accepted the key.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
