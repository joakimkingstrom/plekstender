import { useDeferredValue, useState } from 'react'
import { Navigate, NavLink, Outlet } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useLibrarySettings, useMe, usePopularityPreview, useUpdateLibrarySettings } from '../api/hooks'
import { useActiveServer } from '../api/library'
import { Button, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { formatCompact } from '../format'

/** Settings shell: Servers and Listening for everyone, Library for admins. */
export function SettingsLayout() {
  const { data: me } = useMe()
  const tab = ({ isActive }: { isActive: boolean }) =>
    `border-b-2 px-1 pb-2 text-sm font-medium ${
      isActive ? 'border-accent text-text' : 'border-transparent text-muted hover:text-text'
    }`
  return (
    <>
      <PageTitle>Settings</PageTitle>
      <nav className="mb-6 flex gap-6 border-b border-border" aria-label="Settings">
        <NavLink to="/settings" end className={tab}>
          Servers
        </NavLink>
        <NavLink to="/settings/listening" className={tab}>
          Listening
        </NavLink>
        {me?.is_admin && (
          <NavLink to="/settings/library" className={tab}>
            Library
          </NavLink>
        )}
      </nav>
      <Outlet />
    </>
  )
}

type Settings = Schemas['LibrarySettings']

const LIMITS: Record<keyof Settings, { min: number; max: number }> = {
  album_top_n: { min: 1, max: 20 },
  artist_top_n: { min: 1, max: 50 },
  incremental_minutes: { min: 10, max: 1440 },
  full_days: { min: 1, max: 30 },
}

function NumberField({
  label,
  help,
  suffix,
  value,
  limits,
  onChange,
}: {
  label: string
  help: string
  suffix: string
  value: number
  limits: { min: number; max: number }
  onChange: (value: number) => void
}) {
  const invalid = !Number.isInteger(value) || value < limits.min || value > limits.max
  return (
    <label className="block">
      <span className="block text-sm font-medium">{label}</span>
      <span className="mb-2 block text-xs text-muted">{help}</span>
      <span className="flex items-center gap-2">
        <input
          type="number"
          inputMode="numeric"
          min={limits.min}
          max={limits.max}
          step={1}
          value={Number.isNaN(value) ? '' : value}
          onChange={(e) => onChange(e.target.valueAsNumber)}
          aria-invalid={invalid}
          className={`w-24 rounded-lg border bg-surface-2 px-3 py-2 text-sm tabular-nums ${
            invalid ? 'border-danger' : 'border-border'
          }`}
        />
        <span className="text-sm text-muted">{suffix}</span>
      </span>
      {invalid && (
        <span className="mt-1 block text-xs text-danger">
          Between {limits.min} and {limits.max}.
        </span>
      )}
    </label>
  )
}

export function LibrarySettingsPage() {
  const me = useMe()
  const settings = useLibrarySettings()
  if (me.isPending) return <Spinner />
  if (!me.data?.is_admin) return <Navigate to="/settings" replace />
  if (settings.isPending) return <Spinner />
  if (settings.isError) return <Notice tone="error">{errorMessage(settings.error)}</Notice>
  return <LibrarySettingsForm initial={settings.data} />
}

function LibrarySettingsForm({ initial }: { initial: Settings }) {
  const [form, setForm] = useState<Settings>(initial)
  const [saved, setSaved] = useState<Settings>(initial)
  const [notice, setNotice] = useState<string | null>(null)
  const update = useUpdateLibrarySettings()
  const set = (key: keyof Settings) => (value: number) => {
    setNotice(null)
    setForm({ ...form, [key]: value })
  }

  const valid = (Object.keys(LIMITS) as (keyof Settings)[]).every((key) => {
    const v = form[key]
    return Number.isInteger(v) && v >= LIMITS[key].min && v <= LIMITS[key].max
  })
  const dirty = (Object.keys(form) as (keyof Settings)[]).some((key) => form[key] !== saved[key])
  const popularityChanged =
    form.album_top_n !== saved.album_top_n || form.artist_top_n !== saved.artist_top_n

  const save = () => {
    const rescoring = popularityChanged
    update.mutate(form, {
      onSuccess: (data) => {
        setSaved(data)
        setForm(data)
        setNotice(
          rescoring
            ? 'Saved. Popularity is being recalculated in the background; rankings update within a minute.'
            : 'Saved.',
        )
      },
    })
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <Card title="Popularity">
          <p className="mb-4 text-sm text-muted">
            Plex only knows how popular <em>tracks</em> are (their Last.fm listener counts). Albums
            and artists are scored by adding up their most popular tracks. Counting only the top
            few stops artists with huge discographies or lots of compilations from outranking
            artists with a handful of big hits.
          </p>
          <div className="space-y-5">
            <NumberField
              label="Album popularity"
              help="Sum of the album's most popular tracks."
              suffix="top tracks"
              value={form.album_top_n}
              limits={LIMITS.album_top_n}
              onChange={set('album_top_n')}
            />
            <NumberField
              label="Artist popularity"
              help="Sum of the artist's most popular songs. Remasters and other releases of the same song count once."
              suffix="top songs"
              value={form.artist_top_n}
              limits={LIMITS.artist_top_n}
              onChange={set('artist_top_n')}
            />
          </div>
        </Card>

        <Card title="Sync schedule">
          <div className="space-y-5">
            <NumberField
              label="Check for changes every"
              help="Picks up new plays, ratings and library changes."
              suffix="minutes"
              value={form.incremental_minutes}
              limits={LIMITS.incremental_minutes}
              onChange={set('incremental_minutes')}
            />
            <NumberField
              label="Full sync every"
              help="Re-reads whole libraries; this is also how removed albums and tracks disappear."
              suffix="days"
              value={form.full_days}
              limits={LIMITS.full_days}
              onChange={set('full_days')}
            />
          </div>
        </Card>

        <p className="text-xs text-muted">These settings apply to everyone using this instance.</p>
        {update.isError && <Notice tone="error">{errorMessage(update.error)}</Notice>}
        {notice && <Notice>{notice}</Notice>}
        <div className="flex gap-2">
          <Button variant="primary" disabled={!valid || !dirty} busy={update.isPending} onClick={save}>
            Save
          </Button>
          <Button variant="ghost" disabled={!dirty} onClick={() => { setNotice(null); setForm(saved) }}>
            Reset
          </Button>
        </div>
      </div>

      <RankingPreview artistTopN={form.artist_top_n} changed={popularityChanged} />
    </div>
  )
}

function RankingPreview({ artistTopN, changed }: { artistTopN: number; changed: boolean }) {
  const { active } = useActiveServer()
  const deferredN = useDeferredValue(artistTopN)
  const preview = usePopularityPreview(active?.id ?? null, deferredN)

  return (
    <Card title="Top artists preview">
      <p className="mb-4 text-sm text-muted">
        Your most popular artists{active ? ` on ${active.name}` : ''} when counting their top{' '}
        {Number.isInteger(artistTopN) ? artistTopN : '…'} songs.
        {changed && ' Arrows show the change from the current ranking.'}
      </p>
      {!active && <Notice>Connect a server and sync it to see a preview.</Notice>}
      {preview.isPending && active && <Spinner small />}
      {preview.isError && <Notice tone="error">{errorMessage(preview.error)}</Notice>}
      {preview.data?.length === 0 && <p className="text-sm text-muted">No popularity data yet.</p>}
      {preview.data && preview.data.length > 0 && (
        <ol className={`divide-y divide-border ${preview.isFetching ? 'opacity-60' : ''}`}>
          {preview.data.map((row, i) => {
            const rank = i + 1
            const delta = row.current_rank - rank
            return (
              <li key={row.rating_key} className="flex items-center gap-3 py-2 text-sm">
                <span className="w-6 text-right text-xs text-muted tabular-nums">{rank}</span>
                <span className="min-w-0 flex-1 truncate">{row.title}</span>
                {changed && delta !== 0 && (
                  <span
                    className={`text-xs tabular-nums ${delta > 0 ? 'text-ok' : 'text-danger'}`}
                    title={`Currently #${row.current_rank}`}
                  >
                    {delta > 0 ? '▲' : '▼'} {Math.abs(delta)}
                  </span>
                )}
                <span className="w-14 text-right text-xs text-muted tabular-nums">{formatCompact(row.popularity)}</span>
              </li>
            )
          })}
        </ol>
      )}
    </Card>
  )
}
