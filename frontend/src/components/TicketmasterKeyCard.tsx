import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useSetTicketmasterKey, useTicketmasterKey } from '../api/live'
import { Button, Card, Notice, Spinner } from './ui'

/** The instance's Ticketmaster key (admins), Live's second source, and today's requests. */
export function TicketmasterKeyCard() {
  const key = useTicketmasterKey(true)
  const save = useSetTicketmasterKey()
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)
  const valid = /^[A-Za-z0-9]{16,64}$/.test(value.trim())
  return (
    <Card title="Ticketmaster API key">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          A second source for the Live tab: all gigs near each person are read and matched against their artists.
          Create a free app at{' '}
          <a href="https://developer.ticketmaster.com" target="_blank" rel="noreferrer" className="text-accent underline">
            developer.ticketmaster.com
          </a>{' '}
          and paste its Consumer Key (5,000 requests a day).
        </p>
        {key.isPending ? (
          <Spinner small />
        ) : key.isError ? (
          <Notice tone="error">{errorMessage(key.error)}</Notice>
        ) : (
          <div className="space-y-1 text-sm">
            <p>
              {key.data.source === 'settings'
                ? 'A key is set on this page.'
                : key.data.source === 'environment'
                  ? 'Using TICKETMASTER_API_KEY from the environment. A key saved here takes precedence.'
                  : 'No key is set.'}
            </p>
            {key.data.configured && (
              <p className="text-muted">
                Today: {key.data.calls} of {key.data.cap} requests.
              </p>
            )}
          </div>
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
            <span className="mb-1 block text-sm font-medium">{key.data?.configured ? 'Replace key' : 'Consumer Key'}</span>
            <input
              type="password"
              value={value}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => {
                setSaved(false)
                setValue(e.target.value)
              }}
              placeholder="Letters and digits"
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
        {saved && <Notice>Saved. Ticketmaster accepted the key.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
