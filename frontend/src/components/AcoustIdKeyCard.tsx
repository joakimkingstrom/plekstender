import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useAcoustIdKey, useSetAcoustIdKey } from '../api/files'
import { Button, Card, Notice, Spinner } from './ui'

/** The instance's AcoustID application key (admins), for identifying music files by sound. */
export function AcoustIdKeyCard() {
  const key = useAcoustIdKey(true)
  const save = useSetAcoustIdKey()
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)
  const valid = /^[A-Za-z0-9]{6,64}$/.test(value.trim())
  return (
    <Card title="AcoustID application key">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          Used to identify music files by how they sound (Settings → Music files). Register a free application at{' '}
          <a href="https://acoustid.org/new-application" target="_blank" rel="noreferrer" className="text-accent underline">
            acoustid.org/new-application
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
                ? 'Using ACOUSTID_API_KEY from the environment. A key saved here takes precedence.'
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
            <span className="mb-1 block text-sm font-medium">{key.data?.configured ? 'Replace key' : 'Application key'}</span>
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
        {saved && <Notice>Saved. AcoustID accepted the key.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
