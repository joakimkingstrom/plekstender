import { useDeferredValue, useState } from 'react'
import { ColorPicker, Stepper } from '../components/controls'
import { BRAND, PRESETS } from '../accent'
import { Tooltip } from '../components/Tooltip'
import { Navigate, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router'
import { errorMessage, type Schemas } from '../api/client'
import { useLibrarySettings, useMe, usePopularityPreview, useSetPreferences, useUpdateLibrarySettings } from '../api/hooks'
import { useActiveServer } from '../api/library'
import { fontPreviewUrl, useEditFonts, useFanartKey, useFontCatalogue, useFonts, useSetFanartKey, type FontGroupName } from '../api/artwork'
import { Segmented } from '../components/Segmented'
import { Select } from '../components/Select'
import { EXPORT_PARTS, SERVICE_KINDS } from '../settingsSections'
import { FolderSettings } from '../components/FolderSettings'
import { ScanCard } from '../components/MusicFiles'
import { ArtworkSummaryCard } from '../components/FileArtwork'
import { PlexSync } from '../components/PlexSync'
import { TagHistory } from '../components/TagEditor'
import { UnknownFiles } from '../components/UnknownFiles'
import { useFilesSummary } from '../api/files'
import { Button, Card, Notice, PageTitle, Spinner } from '../components/ui'
import { formatCompact } from '../format'

type Section = { to: string; label: string; end?: boolean; children?: Section[] }
type Group = { name: string; sections: Section[] }

/** The Settings sections, in groups: yours, your server's library (owners), and the admin's. */
function settingsGroups(owner: boolean, admin: boolean): Group[] {
  const groups: Group[] = [
    {
      name: 'You',
      sections: [
        { to: '/settings', label: 'Servers', end: true },
        { to: '/settings/scrobbling', label: 'Scrobbling' },
        { to: '/settings/listening', label: 'Listening history' },
        { to: '/settings/game', label: 'Guess the intro' },
        {
          to: '/settings/export',
          label: 'Export & import',
          children: EXPORT_PARTS.filter((p) => !p.owners || owner).map((p) => ({ to: `/settings/export/${p.slug}`, label: p.title })),
        },
      ],
    },
  ]
  if (owner) groups.push({ name: 'Library', sections: [{ to: '/settings/files', label: 'Music files' }] })
  if (admin)
    groups.push({
      name: 'Admin',
      sections: [
        { to: '/settings/users', label: 'Users & access' },
        { to: '/settings/library', label: 'Library tuning' },
        {
          to: '/settings/fonts',
          label: 'Text logo fonts',
          children: [
            { to: '/settings/fonts/offered', label: 'Offered' },
            { to: '/settings/fonts/add', label: 'Add from Google Fonts' },
          ],
        },
        {
          to: '/settings/services',
          label: 'Services',
          children: SERVICE_KINDS.map((k) => ({ to: `/settings/services/${k.slug}`, label: k.title })),
        },
      ],
    })
  return groups
}

/** Settings shell: a grouped sidebar (a section picker on phones) beside the section. */
export function SettingsLayout() {
  const { data: me } = useMe()
  const { active } = useActiveServer()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const groups = settingsGroups(Boolean(active?.is_owner), Boolean(me?.is_admin))
  const sections = groups.flatMap((g) => g.sections.flatMap((s) => [s, ...(s.children ?? [])]))
  const matches = (s: Section) => (s.end ? pathname === s.to || pathname === `${s.to}/` : pathname.startsWith(s.to))
  // The most specific section the address is in (Services → Gigs rather than Services).
  const current = sections.filter(matches).sort((a, b) => b.to.length - a.to.length)[0] ?? sections[0]
  const parent = sections.find((s) => s.children?.includes(current))
  const link = ({ isActive }: { isActive: boolean }) =>
    `block rounded-lg px-3 py-1.5 text-sm ${isActive ? 'bg-surface-2 font-medium text-text' : 'text-muted hover:text-text'}`
  const subLink = ({ isActive }: { isActive: boolean }) =>
    `block rounded-md px-2 py-1 text-sm ${isActive ? 'font-medium text-accent' : 'text-muted hover:text-text'}`
  return (
    <>
      <PageTitle>Settings</PageTitle>
      <div className="grid gap-6 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-8">
        <nav aria-label="Settings" className="hidden lg:block">
          {groups.map((g) => (
            <div key={g.name} className="mb-5">
              <p className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-muted">{g.name}</p>
              {g.sections.map((s) => (
                <div key={s.to}>
                  <NavLink to={s.children ? s.children[0].to : s.to} end={s.end} className={link}>
                    {s.label}
                  </NavLink>
                  {s.children && matches(s) && (
                    <div className="mb-1 ml-3 mt-0.5 border-l border-border pl-2">
                      {s.children.map((c) => (
                        <NavLink key={c.to} to={c.to} className={subLink}>
                          {c.label}
                        </NavLink>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}
        </nav>
        <div className="min-w-0">
          <div className="mb-4 lg:hidden">
            <Select
              label="Settings section"
              value={current.to}
              options={groups.flatMap((g) =>
                g.sections.flatMap((s) =>
                  s.children
                    ? s.children.map((c) => ({ value: c.to, label: `${s.label}: ${c.label}`, group: g.name }))
                    : [{ value: s.to, label: s.label, group: g.name }],
                ),
              )}
              onChange={(to) => navigate(to)}
            />
          </div>
          <h2 className="mb-4 hidden text-lg font-semibold lg:block">
            {parent && <span className="font-normal text-muted">{parent.label} · </span>}
            {current.label}
          </h2>
          <Outlet />
        </div>
      </div>
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
    </div>
  )
}

const FONT_GROUPS: FontGroupName[] = ['Sans serif', 'Serif', 'Display', 'Handwriting', 'Monospace']
const CATALOGUE_PAGE = 24

/** A font's preview (its name, or the preview text, drawn in it). */
function FontPreview({ family, text, className = 'h-7' }: { family: string; text: string; className?: string }) {
  const src = fontPreviewUrl(family, text)
  // Which preview failed (e.g. Google unreachable): the name in plain text instead.
  const [failed, setFailed] = useState<string | null>(null)
  if (failed === src)
    return (
      <span className={`flex items-center text-sm ${className}`} title="No preview">
        {text.trim() || family}
      </span>
    )
  return (
    <img
      src={src}
      alt={`${text.trim() || family} in ${family}`}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(src)}
      className={`font-preview max-w-full object-contain object-left ${className}`}
    />
  )
}

function PreviewWith({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  return (
    <label className="block max-w-md">
      <span className="mb-1 block font-medium">Preview with</span>
      <input
        value={value}
        maxLength={40}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Each font's name (or type an artist)"
        className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
      />
    </label>
  )
}

/** Settings → Text logo fonts → Offered: the fonts the Artwork tab offers (remove, restore). */
export function OfferedFonts() {
  const fonts = useFonts()
  const { remove, reset } = useEditFonts()
  const [sample, setSample] = useState('')
  const error = remove.error ?? reset.error
  const total = (fonts.data ?? []).reduce((n, g) => n + g.fonts.length, 0)
  return (
    <Card
      title={`Offered (${total})`}
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
      <div className="space-y-5 text-sm">
        <p className="text-muted">
          The fonts the Artwork tab offers for text logos. More can be added from{' '}
          <NavLink to="/settings/fonts/add" className="text-accent underline">
            Google Fonts
          </NavLink>
          ; each is downloaded the first time someone uses it.
        </p>
        <PreviewWith value={sample} onChange={setSample} />
        {error && <Notice tone="error">{errorMessage(error)}</Notice>}
        {fonts.isPending ? (
          <Spinner small />
        ) : fonts.isError ? (
          <Notice tone="error">{errorMessage(fonts.error)}</Notice>
        ) : (
          <div className="gap-6 sm:columns-2 lg:columns-3">
            {fonts.data.map((g) => (
              <div key={g.name} className="mb-5 break-inside-avoid">
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
                  {g.name} <span className="font-normal">({g.fonts.length})</span>
                </h4>
                <ul className="space-y-1">
                  {g.fonts.map((f) => (
                    <li key={f} className="group flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-surface-2">
                      <div className="min-w-0 flex-1">
                        <FontPreview family={f} text={sample} />
                        {sample.trim() && <span className="block text-[11px] text-muted">{f}</span>}
                      </div>
                      <button
                        type="button"
                        aria-label={`Remove ${f}`}
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(f)}
                        className="flex size-6 shrink-0 items-center justify-center rounded-full text-muted hover:bg-border hover:text-text"
                      >
                        ×
                      </button>
                    </li>
                  ))}
                  {g.fonts.length === 0 && <li className="px-2 text-xs text-muted">None</li>}
                </ul>
              </div>
            ))}
          </div>
        )}
        <p className="text-muted">
          Saving artist images into artist folders (artist.jpg) is under{' '}
          <NavLink to="/settings/files" className="text-accent underline">
            Music files
          </NavLink>
          .
        </p>
      </div>
    </Card>
  )
}

/** Settings → Text logo fonts → Add: browse Google Fonts and add some. */
export function AddFonts() {
  const { add } = useEditFonts()
  const [sample, setSample] = useState('')
  const [justAdded, setJustAdded] = useState<{ name: string; group: string } | null>(null)
  const addFont = (name: string) =>
    add.mutate(name, {
      onSuccess: (groups) => {
        const group = groups.find((g) => g.fonts.includes(name))?.name ?? ''
        setJustAdded({ name, group })
      },
    })
  return (
    <Card>
      <div className="space-y-5 text-sm">
        <p className="text-muted">
          Google Fonts, most used first. Added fonts join their group in the{' '}
          <NavLink to="/settings/fonts/offered" className="text-accent underline">
            offered list
          </NavLink>
          .
        </p>
        <PreviewWith value={sample} onChange={setSample} />
        {add.error && <Notice tone="error">{errorMessage(add.error)}</Notice>}
        <FontBrowser sample={sample} onAdd={addFont} adding={add.isPending ? add.variables : null} added={justAdded} />
      </div>
    </Card>
  )
}

/** Browse Google Fonts (most used first) and add fonts; their group comes from Google. */
function FontBrowser({
  sample,
  onAdd,
  adding,
  added,
}: {
  sample: string
  onAdd: (name: string) => void
  adding: string | null | undefined
  added: { name: string; group: string } | null
}) {
  const [q, setQ] = useState('')
  const [group, setGroup] = useState<FontGroupName | null>(null)
  const [limit, setLimit] = useState(CATALOGUE_PAGE)
  const query = useDeferredValue(q.trim())
  const page = useFontCatalogue(query, group, limit)
  const chip = (active: boolean) =>
    `rounded-full px-3 py-1 text-xs font-medium ${active ? 'bg-accent text-on-accent' : 'bg-surface-2 text-muted hover:text-text'}`

  return (
    <section aria-labelledby="add-fonts" className="space-y-3">
      <h3 id="add-fonts" className="sr-only">
        Add from Google Fonts
      </h3>
      <input
        type="search"
        value={q}
        onChange={(e) => {
          setQ(e.target.value)
          setLimit(CATALOGUE_PAGE)
        }}
        placeholder="Search fonts, e.g. roboto"
        aria-label="Search Google Fonts"
        className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
      />
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Font group">
        {[null, ...FONT_GROUPS].map((g) => (
          <button
            key={g ?? 'all'}
            type="button"
            aria-pressed={group === g}
            onClick={() => {
              setGroup(g)
              setLimit(CATALOGUE_PAGE)
            }}
            className={chip(group === g)}
          >
            {g ?? 'All'}
          </button>
        ))}
      </div>
      {added && (
        <p className="text-sm text-ok" role="status">
          {added.name} added{added.group ? ` to ${added.group}` : ''}: it's now offered on the Artwork page.
        </p>
      )}
      {page.isPending ? (
        <Spinner small />
      ) : page.isError ? (
        <Notice tone="error">{errorMessage(page.error)}</Notice>
      ) : !page.data.available ? (
        <Notice tone="warn">Google Fonts couldn't be reached. Try again in a while.</Notice>
      ) : page.data.items.length === 0 ? (
        <p className="text-muted">No Google Fonts match “{query}”.</p>
      ) : (
        <>
          <ul className={`grid gap-2 sm:grid-cols-2 lg:grid-cols-3 ${page.isPlaceholderData ? 'opacity-60' : ''}`}>
            {page.data.items.map((f) => (
              <li key={f.name} className="flex items-center gap-3 rounded-xl border border-border p-3">
                <div className="min-w-0 flex-1 space-y-1">
                  <FontPreview family={f.name} text={sample} className="h-8" />
                  <p className="truncate text-xs text-muted">
                    {f.name} · {f.group}
                  </p>
                </div>
                {f.offered || added?.name === f.name ? (
                  <span className="shrink-0 text-xs text-ok">Added ✓</span>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm" className="shrink-0"
                    busy={adding === f.name}
                    disabled={Boolean(adding)}
                    onClick={() => onAdd(f.name)}
                    aria-label={`Add ${f.name}`}
                  >
                    {adding === f.name ? 'Adding…' : 'Add'}
                  </Button>
                )}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted">
            {formatCompact(page.data.total)} fonts{query || group ? ' match' : ''}, most used first.
          </p>
          {page.data.items.length < page.data.total && (
            <Button variant="ghost" busy={page.isFetching} onClick={() => setLimit((n) => n + CATALOGUE_PAGE)}>
              Show more
            </Button>
          )}
        </>
      )}
    </section>
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
    save.mutate({ accent: hex === BRAND ? null : hex })
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
      <Card title="On your phone">
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-medium">
              Open on
            </span>
            <Segmented
              label="Open on"
              value={me.data?.preferences?.phone_start ?? 'rate'}
              options={[
                ['rate', 'Rate'],
                ['home', 'Home'],
              ]}
              onChange={(phone_start) => save.mutate({ phone_start })}
            />
          </div>
          <p className="text-muted">
            For the full-screen app: in Safari, tap Share → Add to Home Screen (Chrome: ⋮ → Add to Home screen), then
            open Plekstender from its icon.
          </p>
        </div>
      </Card>
    </div>
  )
}

/** Artwork settings (admins): the text logo fonts. */
/** Settings → Text logo fonts (admins): the offered fonts, or adding some. */
export function FontsSettingsPage() {
  const me = useMe()
  const { part } = useParams()
  if (me.isPending) return <Spinner />
  if (!me.data?.is_admin) return <Navigate to="/settings" replace />
  if (part === 'add') return <AddFonts />
  if (part === 'offered') return <OfferedFonts />
  return <Navigate to="/settings/fonts/offered" replace />
}

function PlexSyncCard({ serverId }: { serverId: number }) {
  const summary = useFilesSummary(serverId)
  if (!summary.data?.mapped || !summary.data.files) return null
  return (
    <Card title="Plex's metadata into the files">
      <p className="mb-3 text-sm text-muted">
        Make your files' tags match Plex across the whole library (for one artist: on their page). Only tags that
        differ are changed, and the whole run can be undone.
      </p>
      <PlexSync serverId={serverId} scope="library" canWrite={summary.data.tag_editing} />
    </Card>
  )
}

function ArtworkCard({ serverId }: { serverId: number }) {
  const summary = useFilesSummary(serverId)
  if (!summary.data?.mapped || !summary.data.files) return null
  return <ArtworkSummaryCard serverId={serverId} canWrite={summary.data.tag_editing} />
}

function UnknownFilesCard({ serverId }: { serverId: number }) {
  const summary = useFilesSummary(serverId)
  const me = useMe()
  if (!summary.data?.mapped || !summary.data.files) return null
  return <UnknownFiles serverId={serverId} canWrite={summary.data.tag_editing} isAdmin={Boolean(me.data?.is_admin)} />
}

/** The music folder and tag scans (server owners). */
export function FilesSettingsPage() {
  const { active, servers } = useActiveServer()
  if (servers.isPending) return <Spinner />
  if (!active?.is_owner) return <Navigate to="/settings" replace />
  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <FolderSettings serverId={active.id} />
        <UnknownFilesCard serverId={active.id} />
      </div>
      <div className="space-y-4">
        <ScanCard serverId={active.id} />
        <PlexSyncCard serverId={active.id} />
        <ArtworkCard serverId={active.id} />
        <TagHistory serverId={active.id} />
      </div>
    </div>
  )
}
