import { useState } from 'react'
import { errorMessage } from '../api/client'
import { useImportGigs } from '../api/gigs'
import { FilePicker } from './controls'
import { Button, Card, Notice } from './ui'

const linkButton = 'inline-flex items-center rounded-lg bg-surface-2 px-4 py-2 text-sm font-semibold hover:bg-border'

/** Gigs you've been to, in: from setlist.fm, a CSV file or a calendar file. */
export function GigImportCard() {
  const importer = useImportGigs()
  const [username, setUsername] = useState('')
  const result = importer.data
  return (
    <Card title="Import gigs">
      <div className="space-y-4 text-sm">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (username.trim()) importer.mutate({ setlistfm: username.trim() })
          }}
        >
          <label className="min-w-0 flex-1 space-y-1">
            <span className="block font-medium">setlist.fm username</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="As on your setlist.fm profile"
              className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2"
            />
          </label>
          <Button type="submit" disabled={!username.trim()} busy={importer.isPending && 'setlistfm' in (importer.variables ?? {})}>
            Import the concerts I went to
          </Button>
        </form>
        <FilePicker
          accept=".csv,text/csv"
          label="A CSV file"
          hint="With a header row: date and artist at least (venue, city, country, support, festival, notes are read too)."
          onFile={(file) => importer.mutate({ file, kind: 'csv' })}
        />
        <FilePicker
          accept=".ics,text/calendar"
          label="A calendar file (.ics)"
          hint="Its past events, e.g. from Songkick or your own calendar."
          onFile={(file) => importer.mutate({ file, kind: 'ical' })}
        />
        {result && (
          <Notice>
            Added {result.added}
            {result.updated > 0 && `, updated ${result.updated}`}
            {result.skipped > 0 && `, ${result.skipped} already there`}.
            {result.problems.length > 0 && (
              <ul className="mt-1 list-disc pl-5 text-xs">
                {result.problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            )}
          </Notice>
        )}
        {importer.isError && <Notice tone="error">{errorMessage(importer.error)}</Notice>}
      </div>
    </Card>
  )
}

/** Gigs you've been to, out: CSV, JSON or a calendar file. */
export function GigExportCard() {
  return (
    <Card title="Export gigs">
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap gap-2">
          <a className={linkButton} href="/api/gigs/export.csv" download>
            CSV
          </a>
          <a className={linkButton} href="/api/gigs/export.json" download>
            JSON
          </a>
          <a className={linkButton} href="/api/gigs/export.ics" download>
            Calendar (.ics)
          </a>
        </div>
        <p className="text-xs text-muted">
          No gig-tracking service lets apps add gigs for you (setlist.fm can only be read), so to take your gigs elsewhere,
          import one of these files there.
        </p>
      </div>
    </Card>
  )
}
