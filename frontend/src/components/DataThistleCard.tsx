import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useDataThistle, useSetDataThistle } from '../api/live'
import { formatDate, formatRelative } from '../format'
import { Button, Card, Notice, Spinner } from './ui'

const JWT = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/

/** Data Thistle's tokens (admins): UK listings for the Live tab, renewed by Plekstender. */
export function DataThistleCard() {
  const status = useDataThistle(true)
  const save = useSetDataThistle()
  const [access, setAccess] = useState('')
  const [refresh, setRefresh] = useState('')
  const [saved, setSaved] = useState(false)
  const valid = JWT.test(access.trim()) && JWT.test(refresh.trim()) && access.trim() !== refresh.trim()
  const data = status.data
  return (
    <Card title="Data Thistle">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          UK listings for the Live tab, including small local shows. Register for free at{' '}
          <a href="https://api.datathistle.com" target="_blank" rel="noreferrer" className="text-accent underline">
            api.datathistle.com
          </a>
          , then copy the access token and the refresh token from its “Get Access Token” page. Plekstender renews them
          before they run out. 1,000 requests a month; listings are looked up when someone opens Live.
        </p>
        {status.isPending ? (
          <Spinner small />
        ) : status.isError ? (
          <Notice tone="error">{errorMessage(status.error)}</Notice>
        ) : !data ? null : data.configured ? (
          <div className="space-y-1 text-sm">
            <p>
              Access token until {data.access_expires ? formatDate(data.access_expires) : '?'}; refresh token until{' '}
              {data.refresh_expires ? formatDate(data.refresh_expires) : '?'}.
              {data.renewed_at && ` Last renewed ${formatRelative(data.renewed_at)}.`}
            </p>
            <p className="text-muted">
              Today: {data.calls} of {data.cap} requests.
              {data.remaining != null && ` This month: ${data.remaining}${data.limit != null ? ` of ${data.limit}` : ''} left.`}
            </p>
            {data.error && <Notice tone="warn">Renewing failed: {data.error} Paste new tokens if it keeps failing.</Notice>}
          </div>
        ) : (
          <p className="text-sm">No tokens are set.</p>
        )}
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid)
              save.mutate(
                { access_token: access.trim(), refresh_token: refresh.trim() },
                {
                  onSuccess: () => {
                    setAccess('')
                    setRefresh('')
                    setSaved(true)
                  },
                },
              )
          }}
        >
          {(
            [
              ['Access token', access, setAccess],
              ['Refresh token', refresh, setRefresh],
            ] as const
          ).map(([label, value, set]) => (
            <label key={label} className="block">
              <span className="mb-1 block text-sm font-medium">{label}</span>
              <input
                type="password"
                value={value}
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => {
                  setSaved(false)
                  set(e.target.value)
                }}
                placeholder="eyJ…"
                className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm"
              />
            </label>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending && save.variables !== null}>
              Check and save
            </Button>
            {data?.configured && (
              <Button variant="ghost" busy={save.isPending && save.variables === null} onClick={() => save.mutate(null)}>
                Remove
              </Button>
            )}
          </div>
          <p className="text-xs text-muted">
            Renewing the tokens on the Data Thistle page replaces the ones Plekstender holds: paste the new ones here then.
          </p>
        </form>
        {saved && <Notice>Saved. Data Thistle accepted the token.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
