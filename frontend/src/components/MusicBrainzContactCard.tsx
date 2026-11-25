import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, errorMessage, unwrap } from '../api/client'
import { Button, Card, Notice, Spinner } from './ui'

const KEY = ['admin', 'musicbrainz'] as const
// An email address or a web page (what the server accepts).
const VALID = /^(?:[^\s@()]+@[^\s@()]+\.[^\s@()]+|https?:\/\/[^\s()]+)$/

/** The contact sent to MusicBrainz with every request (admins). */
export function MusicBrainzContactCard() {
  const qc = useQueryClient()
  const current = useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/admin/musicbrainz')) })
  const save = useMutation({
    mutationFn: (contact: string | null) => unwrap(api.PUT('/api/admin/musicbrainz', { body: { contact } })),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  })
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)
  const trimmed = value.trim()
  const valid = trimmed.length <= 200 && VALID.test(trimmed)
  return (
    <Card title="Contact for MusicBrainz">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          MusicBrainz asks apps to say how to reach whoever runs them, and may slow down the ones that don't. An email
          address or a web page; it's sent only to MusicBrainz, with each request.
        </p>
        {current.isPending ? (
          <Spinner small />
        ) : current.isError ? (
          <Notice tone="error">{errorMessage(current.error)}</Notice>
        ) : (
          <p className="text-sm">
            {current.data.source === 'settings' ? (
              <>
                Set on this page: <span className="font-medium">{current.data.contact}</span>
              </>
            ) : current.data.source === 'environment' ? (
              <>
                Using MUSICBRAINZ_CONTACT from the environment: <span className="font-medium">{current.data.contact}</span>. One
                saved here takes precedence.
              </>
            ) : (
              'No contact is set.'
            )}
          </p>
        )}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid)
              save.mutate(trimmed, {
                onSuccess: () => {
                  setValue('')
                  setSaved(true)
                },
              })
          }}
        >
          <label className="min-w-0 flex-1">
            <span className="mb-1 block text-sm font-medium">{current.data?.contact ? 'Change it' : 'Email address or web page'}</span>
            <input
              type="text"
              value={value}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => {
                setSaved(false)
                setValue(e.target.value)
              }}
              placeholder="you@example.com or https://…"
              className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
            />
          </label>
          <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending && save.variables !== null}>
            Save
          </Button>
          {current.data?.source === 'settings' && (
            <Button variant="ghost" busy={save.isPending && save.variables === null} onClick={() => save.mutate(null)}>
              Remove
            </Button>
          )}
        </form>
        {trimmed && !valid && <p className="text-xs text-muted">An email address, or a web page starting with https://.</p>}
        {saved && <Notice>Saved. The worker uses it within a minute.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
}
