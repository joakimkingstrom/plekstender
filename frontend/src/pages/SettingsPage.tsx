import { useDeferredValue, useState } from 'react'
import { ColorPicker, Stepper } from '../components/controls'
import { BRAND, PRESETS } from '../accent'
import { Tooltip } from '../components/Tooltip'
import { Navigate, NavLink, Outlet } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useLibrarySettings, useMe, usePopularityPreview, useSetPreferences, useUpdateLibrarySettings } from '../api/hooks'
import { useActiveServer } from '../api/library'
import { useEditFonts, useFanartKey, useFonts, useSetFanartKey, type FontGroupName } from '../api/artwork'
import { Select } from '../components/Select'
import { Button, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { formatCompact } from '../format'

/** Settings shell: personal tabs for everyone; Library and Admin for admins. */
export function SettingsLayout() {
  const { data: me } = useMe()
  const tab = ({ isActive }: { isActive: boolean }) =>
    `border-b-2 px-1 pb-2 text-sm font-medium ${
      isActive ? 'border-accent text-text' : 'border-transparent text-muted hover:text-text'
    }`
  return (
    <>
      <PageTitle>Settings</PageTitle>
      <nav className="no-scrollbar mb-6 flex gap-6 overflow-x-auto whitespace-nowrap border-b border-border" aria-label="Settings">
        <NavLink to="/settings" end className={tab}>
          Servers
        </NavLink>
        <NavLink to="/settings/listening" className={tab}>
          Listening
        </NavLink>
        <NavLink to="/settings/connections" className={tab}>
          Connections
        </NavLink>
        <NavLink to="/settings/export" className={tab}>
          Export / import
        </NavLink>
        <NavLink to="/settings/appearance" className={tab}>
          Appearance
        </NavLink>
        {me?.is_admin && (
          <>
            <NavLink to="/settings/library" className={tab}>
              Library
            </NavLink>
            <NavLink to="/settings/admin" className={tab}>
              Admin
            </NavLink>
          </>
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
    <div className="block">
      <span className="block text-sm font-medium">{label}</span>
      <span className="mb-2 block text-xs text-muted">{help}</span>
      <span className="flex items-center gap-2">
        <Stepper label={label} value={value} min={limits.min} max={limits.max} onChange={onChange} invalid={invalid} />
        <span className="text-sm text-muted">{suffix}</span>
      </span>
      {invalid && (
        <span className="mt-1 block text-xs text-danger">
          Between {limits.min} and {limits.max}.
        </span>
      )}
    </div>
  )
}

export function LibrarySettingsPage() {
  const me = useMe()
  const settings = useLibrarySettings()
  if (me.isPending) return <Spinner />
  if (!me.data?.is_admin) return <Navigate to="/settings" replace />
  if (settings.isPending) return <Spinner />
  if (settings.isError) return <Notice tone="error">{errorMessage(settings.error)}</Notice>
  return (
    <div className="space-y-4">
      <LibrarySettingsForm initial={settings.data} />
      <FontsCard />
    </div>
  )
}

const FONT_GROUPS: FontGroupName[] = ['Sans serif', 'Serif', 'Display', 'Handwriting', 'Monospace']

function FontsCard() {
  const fonts = useFonts()
  const { add, remove, reset } = useEditFonts()
  const [name, setName] = useState('')
  const [group, setGroup] = useState<FontGroupName>('Display')
  const error = add.error ?? remove.error ?? reset.error
  return (
    <Card
      title="Text logo fonts"
      action={
        <Button
          variant="ghost"
          busy={reset.isPending}
          onClick={() => {
            if (window.confirm('Go back to the default list of 44 fonts?')) reset.mutate()
          }}
        >
          Restore defaults
        </Button>
      }
    >
      <div className="space-y-4 text-sm">
        <p className="text-muted">
          The fonts the Artwork page offers for text logos. Any family on{' '}
          <a href="https://fonts.google.com" target="_blank" rel="noreferrer" className="text-accent underline">
            fonts.google.com
          </a>{' '}
          can be added by its name; it's downloaded the first time someone uses it.
        </p>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (name.trim()) add.mutate({ name: name.trim(), group }, { onSuccess: () => setName('') })
          }}
        >
          <label className="min-w-0 flex-1 basis-48">
            <span className="mb-1 block font-medium">Font name</span>
            <input
              value={name}
              maxLength={64}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Bungee Shade"
              className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
            />
          </label>
          <Select
            label="Group"
            value={group}
            onChange={setGroup}
            options={FONT_GROUPS.map((g) => ({ value: g, label: g }))}
          />
          <Button type="submit" variant="primary" disabled={!name.trim()} busy={add.isPending}>
            Check and add
          </Button>
        </form>
        {error && <Notice tone="error">{errorMessage(error)}</Notice>}
        {fonts.isPending ? (
          <Spinner small />
        ) : fonts.isError ? (
          <Notice tone="error">{errorMessage(fonts.error)}</Notice>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {fonts.data.map((g) => (
              <div key={g.name}>
                <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
                  {g.name} <span className="font-normal">({g.fonts.length})</span>
                </h3>
                <ul className="flex flex-wrap gap-1.5">
                  {g.fonts.map((f) => (
                    <li
                      key={f}
                      className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 py-0.5 pl-2.5 pr-0.5 text-xs"
                    >
                      {f}
                      <button
                        type="button"
                        aria-label={`Remove ${f}`}
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(f)}
                        className="flex size-5 items-center justify-center rounded-full text-muted hover:bg-border hover:text-text"
                      >
                        ×
                      </button>
                    </li>
                  ))}
                  {g.fonts.length === 0 && <li className="text-xs text-muted">None</li>}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  )
}

export function FanartKeyCard() {
  const key = useFanartKey(true)
  const save = useSetFanartKey()
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)
  const valid = /^[0-9A-Fa-f]{32}$/.test(value.trim())
  return (
    <Card title="fanart.tv API key">
      <div className="space-y-3">
        <p className="text-sm text-muted">
          Used by the Artwork page to find artist logos. Get a free key at{' '}
          <a href="https://fanart.tv/get-an-api-key/" target="_blank" rel="noreferrer" className="text-accent underline">
            fanart.tv/get-an-api-key
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
                ? 'Using FANART_API_KEY from the environment. A key saved here takes precedence.'
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
              placeholder="32 characters"
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
        {saved && <Notice>Saved. fanart.tv accepted the key.</Notice>}
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
      </div>
    </Card>
  )
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
<Tooltip content={`Currently #${row.current_rank}`}>
                  <span
                    className={`text-xs tabular-nums ${delta > 0 ? 'text-ok' : 'text-danger'}`}
                  >
                    {delta > 0 ? '▲' : '▼'} {Math.abs(delta)}
                  </span>
                  </Tooltip>
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

export function AppearancePage() {
  const me = useMe()
  const save = useSetPreferences()
  if (me.isPending) return <Spinner />
  const current = me.data?.preferences?.accent ?? BRAND
  const set = (hex: string) =>
    save.mutate({ skip_threshold: 80, ...me.data?.preferences, accent: hex === BRAND ? null : hex })
  const isPreset = PRESETS.some(([, hex]) => hex === current)
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Accent colour">
        <p className="mb-4 text-sm text-muted">
          Used for buttons, links, stars and charts. It's saved to your account, so it follows you to other devices.
          Very light or dark colours are adjusted a little so they stay readable in both themes.
        </p>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Accent colour">
          {PRESETS.map(([name, hex]) => (
            <button
              key={hex}
              type="button"
              role="radio"
              aria-checked={hex === current}
              aria-label={name}
              onClick={() => set(hex)}
              className={`flex flex-col items-center gap-1 rounded-lg p-1.5 text-xs ${hex === current ? 'bg-surface-2' : 'hover:bg-surface-2'}`}
            >
              <span
                className={`size-8 rounded-full ${hex === current ? 'ring-2 ring-text ring-offset-2 ring-offset-surface' : ''}`}
                style={{ background: hex }}
              />
              {name}
            </button>
          ))}
        </div>
        <div className="mt-4 flex items-center gap-3 text-sm">
          <span>{isPreset ? 'Or pick your own' : 'Your colour'}</span>
          <ColorPicker label="Custom accent colour" value={current} onChange={set} />
        </div>
        {save.isError && <div className="mt-3"><Notice tone="error">{errorMessage(save.error)}</Notice></div>}
        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-border pt-4">
          <Button variant="primary">Example button</Button>
          <span className="text-sm text-accent underline">A link</span>
          <span className="text-accent" aria-hidden>★★★★☆</span>
        </div>
      </Card>
    </div>
  )
}
