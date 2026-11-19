import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useJamBaseKey, useSetJamBaseKey } from '../api/live'
import { formatRelative } from '../format'
import { Button, Card, Notice, Spinner } from './ui'

/** The instance's JamBase key (admins), for the Live tab, and this month's requests. */
export function JamBaseKeyCard() {
  const [check, setCheck] = useState(false)
  const key = useJamBaseKey(true, check)
  const save = useSetJamBaseKey()
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)
  const valid = /^jbd_[A-Za-z0-9_-]{8,200}$/.test(value.trim())
  const quota = key.data?.quota as { usedCalls?: number; quota?: number | null; periodEnd?: string } | null | undefined
  return (
    <Card title="JamBase API key">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          Finds upcoming shows for the Live tab. The free Developer plan at{' '}
          <a href="https://data.jambase.com" target="_blank" rel="noreferrer" className="text-accent underline">
            data.jambase.com
          </a>{' '}
          is for non-commercial use and has 1,000 requests a month; requests past that are billed, so Plekstender stops at
          its own limit below.
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
                  ? 'Using JAMBASE_API_KEY from the environment. A key saved here takes precedence.'
                  : 'No key is set.'}
            </p>
            {key.data.configured && (
              <p className="text-muted">
                This month: {key.data.calls} of {key.data.cap} requests.
                {quota?.usedCalls != null && (
                  <>
                    {' '}
                    JamBase counted {quota.usedCalls}
                    {quota.quota != null && ` of ${quota.quota}`}
                    {key.data.quota_checked_at && ` (${formatRelative(key.data.quota_checked_at)})`}.
                  </>
                )}{' '}
                <button type="button" className="text-accent underline" onClick={() => setCheck(true)} disabled={check && key.isFetching}>
                  Check now
                </button>
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
              placeholder="jbd_…"
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
        {saved && <Notice>Saved. JamBase accepted the key.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
