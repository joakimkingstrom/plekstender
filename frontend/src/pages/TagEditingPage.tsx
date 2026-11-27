import { useDeferredValue, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import {
  bulk,
  DEFAULT_TEMPLATE,
  useAlbumIssues,
  useFilesSummary,
  useRefreshFiles,
  type AlbumIssue,
  type AlbumIssues,
  type MbBest,
  type RenamePlan,
} from '../api/files'
import { Dialog } from '../components/Dialog'
import { DuplicatesDialog } from '../components/DuplicatesDialog'
import { LyricsDialog } from '../components/LyricsDialog'
import { Artwork } from '../components/library'
import { Select } from '../components/Select'
import { Badge, Button, Notice, Spinner } from '../components/ui'
import { useLibraryServer } from '../components/useLibraryServer'
import { formatCount } from '../format'
import { storedTemplate, storeTemplate } from '../renameTemplate'

const PAGE = 50

const ISSUES: { value: AlbumIssue; label: string }[] = [
  { value: 'any', label: 'Anything to look at' },
  { value: 'differs', label: 'Differs from Plex' },
  { value: 'no_mbid', label: 'No MusicBrainz ID' },
  { value: 'missing', label: 'Missing tags' },
  { value: 'errors', label: 'Unreadable files' },
  { value: 'all', label: 'Every album' },
]

/** Plex's fields as the list names them. */
const FIELD_NAMES: Record<string, string> = {
  title: 'title',
  artist: 'artist',
  album: 'album',
  albumartist: 'album artist',
  track: 'track',
  disc: 'disc',
  year: 'year',
  genre: 'genre',
}

type Action = 'musicbrainz' | 'plex' | 'rename' | 'lyrics' | 'duplicates'

/** Library → Tag editing (server owners): albums whose files need a look, fixed in bulk. */
export function TagEditingPage() {
  const { active, blocker } = useLibraryServer()
  if (blocker) return blocker
  if (!active!.is_owner) return <Notice>Only the server's owner can edit its files' tags.</Notice>
  return <TagEditing serverId={active!.id} />
}

function TagEditing({ serverId }: { serverId: number }) {
  const summary = useFilesSummary(serverId)
  const [issue, setIssue] = useState<AlbumIssue>('any')
  const [query, setQuery] = useState('')
  const q = useDeferredValue(query.trim())
  const [offset, setOffset] = useState(0)
  const page = useAlbumIssues(serverId, issue, q, offset, PAGE)
  const [selected, setSelected] = useState<Map<number, AlbumIssues>>(new Map())
  const [action, setAction] = useState<Action | null>(null)
  // Albums fixed here: hidden from the list while the worker writes their files (the list
  // only catches up once they've been read again).
  const [written, setWritten] = useState<Set<number>>(new Set())
  const onWritten = (key: number) => setWritten((w) => new Set(w).add(key))
  // Closing a dialog lets go of the albums it fixed (not before: its rows stay put).
  const closeAction = () => {
    setSelected((s) => new Map([...s].filter(([key]) => !written.has(key))))
    setAction(null)
  }

  if (summary.isPending) return <Spinner />
  if (summary.isError) return <Notice tone="error">{errorMessage(summary.error)}</Notice>
  if (!summary.data.mapped || summary.data.files === 0)
    return (
      <Notice>
        Plekstender needs to read the music files first: map the music folder and scan it on{' '}
        <Link to="/settings/files" className="text-accent hover:underline">
          Settings → Music files
        </Link>
        .
      </Notice>
    )
  const canWrite = summary.data.tag_editing
  const all = page.data?.items ?? []
  const items = issue === 'all' ? all : all.filter((a) => !written.has(a.rating_key))
  const hiddenHere = all.length - items.length
  const counts = page.data?.counts ?? {}
  const toggle = (a: AlbumIssues) =>
    setSelected((s) => {
      const next = new Map(s)
      if (next.has(a.rating_key)) next.delete(a.rating_key)
      else next.set(a.rating_key, a)
      return next
    })
  const allOnPage = items.length > 0 && items.every((a) => selected.has(a.rating_key))
  const togglePage = () =>
    setSelected((s) => {
      const next = new Map(s)
      for (const a of items) {
        if (allOnPage) next.delete(a.rating_key)
        else next.set(a.rating_key, a)
      }
      return next
    })
  const chosen = [...selected.values()]

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Albums whose files don't match Plex, have no MusicBrainz release ID or are missing tags. Pick a few and look them
        up on MusicBrainz, write Plex's metadata into them, rename their files or add lyrics: each album becomes its own
        edit, which you can undo on its album page.
      </p>
      <div className="flex flex-wrap gap-2">
        <Link
          to="/library/lyrics"
          className="inline-flex items-center rounded-lg bg-surface-2 px-3 py-1.5 text-xs font-semibold hover:bg-border"
        >
          Lyrics for the whole library
        </Link>
        <Button size="sm" onClick={() => setAction('duplicates')}>
          Duplicate files
        </Button>
      </div>
      {!canWrite && (
        <Notice tone="warn">
          Writing to the files is off, so you can look but not fix. Turn on tag editing on{' '}
          <Link to="/settings/files" className="underline">
            Settings → Music files
          </Link>
          .
        </Notice>
      )}
      <div className="flex flex-wrap gap-2">
        <Select
          label="Show"
          value={issue}
          onChange={(v) => {
            setIssue(v)
            setOffset(0)
          }}
          options={ISSUES.map((o) => ({
            value: o.value,
            label: o.value === 'all' ? `${o.label} (${formatCount(page.data?.albums ?? 0)})` : `${o.label} (${formatCount(counts[o.value] ?? 0)})`,
          }))}
        />
        <input
          type="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setOffset(0)
          }}
          placeholder="Search albums and artists"
          aria-label="Search albums and artists"
          className="min-w-0 flex-1 basis-56 rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm"
        />
      </div>
      {(page.data?.not_in_plex ?? 0) > 0 && (
        <p className="text-xs text-muted">
          {formatCount(page.data!.not_in_plex)} {page.data!.not_in_plex === 1 ? "file isn't" : "files aren't"} in Plex at
          all: identify them by sound on{' '}
          <Link to="/settings/files" className="text-accent hover:underline">
            Settings → Music files
          </Link>
          .
        </p>
      )}

      {page.isPending ? (
        <Spinner />
      ) : page.isError ? (
        <Notice tone="error">{errorMessage(page.error)}</Notice>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted">
          {hiddenHere > 0 ? 'Every album on this page has been fixed.' : issue === 'any' && !q ? 'Every album looks fine.' : 'No albums match.'}
        </p>
      ) : (
        <>
          <div className="flex items-center justify-between gap-3 text-xs text-muted">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={allOnPage} onChange={togglePage} />
              Select all on this page
            </label>
            <span>
              {hiddenHere > 0 && `${formatCount(hiddenHere)} just fixed, hidden · `}
              {formatCount(offset + 1)}–{formatCount(offset + all.length)} of {formatCount(page.data.total)}
            </span>
          </div>
          <ul className="divide-y divide-border rounded-xl border border-border bg-surface">
            {items.map((a) => (
              <AlbumRow key={a.rating_key} serverId={serverId} album={a} checked={selected.has(a.rating_key)} onToggle={() => toggle(a)} />
            ))}
          </ul>
          {page.data.total > PAGE && (
            <div className="flex justify-center gap-2">
              <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                Previous
              </Button>
              <Button variant="ghost" disabled={offset + PAGE >= page.data.total} onClick={() => setOffset(offset + PAGE)}>
                Next
              </Button>
            </div>
          )}
        </>
      )}

      {chosen.length > 0 && (
        <div className="sticky bottom-20 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-border bg-surface p-3 shadow-lg md:bottom-4">
          <span className="mr-auto text-sm font-medium">
            {formatCount(chosen.length)} {chosen.length === 1 ? 'album' : 'albums'} selected
          </span>
          <Button variant="primary" onClick={() => setAction('musicbrainz')}>
            Look up on MusicBrainz
          </Button>
          <Button onClick={() => setAction('plex')}>Write Plex's metadata</Button>
          <Button onClick={() => setAction('rename')}>Rename files</Button>
          <Button onClick={() => setAction('lyrics')}>Lyrics</Button>
          <Button variant="ghost" onClick={() => setSelected(new Map())}>
            Clear
          </Button>
        </div>
      )}
      {action === 'musicbrainz' && (
        <BulkMusicBrainz serverId={serverId} albums={chosen} canWrite={canWrite} onWritten={onWritten} onClose={closeAction} />
      )}
      {action === 'plex' && <BulkPlex serverId={serverId} albums={chosen} canWrite={canWrite} onWritten={onWritten} onClose={closeAction} />}
      {action === 'rename' && <BulkRename serverId={serverId} albums={chosen} canWrite={canWrite} onClose={() => setAction(null)} />}
      {action === 'lyrics' && (
        <LyricsDialog serverId={serverId} albums={chosen.map((a) => a.rating_key)} canWrite={canWrite} onClose={() => setAction(null)} />
      )}
      {action === 'duplicates' && <DuplicatesDialog serverId={serverId} canWrite={canWrite} onClose={() => setAction(null)} />}
    </div>
  )
}

function AlbumRow({
  serverId,
  album: a,
  checked,
  onToggle,
}: {
  serverId: number
  album: AlbumIssues
  checked: boolean
  onToggle: () => void
}) {
  const badges: string[] = []
  if (a.errors) badges.push(`${formatCount(a.errors)} unreadable`)
  if (a.missing) badges.push(`${formatCount(a.missing)} missing tags`)
  if (a.differs)
    badges.push(
      `${a.differs === a.files ? 'Differs' : `${formatCount(a.differs)} differ`} from Plex: ${a.differing_fields.map((f) => FIELD_NAMES[f] ?? f).join(', ')}`,
    )
  if (a.no_mbid) badges.push(a.no_mbid === a.files ? 'No MusicBrainz ID' : `${formatCount(a.no_mbid)} without MusicBrainz ID`)
  return (
    <li className="flex items-center gap-3 px-3 py-2">
      <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`Select ${a.title}`} className="shrink-0" />
      <Artwork serverId={serverId} path={a.thumb} title={a.title} size={64} className="size-12 shrink-0" />
      <div className="min-w-0 flex-1">
        <Link to={`/library/albums/${a.rating_key}`} className="block truncate text-sm font-medium hover:underline">
          {a.title}
        </Link>
        <p className="truncate text-xs text-muted">
          {[a.artist_title, a.year, `${formatCount(a.files)} ${a.files === 1 ? 'file' : 'files'}`].filter(Boolean).join(' · ')}
        </p>
        {badges.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {badges.map((b) => (
              <Badge key={b}>{b}</Badge>
            ))}
          </div>
        )}
      </div>
    </li>
  )
}

/** Runs `work` for each album in turn when the dialog opens (and stops when it closes). */
function useEach<T>(albums: AlbumIssues[], work: (album: AlbumIssues) => Promise<T>, start = true) {
  const [results, setResults] = useState<Record<number, { busy?: boolean; value?: T; error?: string }>>({})
  const set = (key: number, patch: { busy?: boolean; value?: T; error?: string }) => setResults((r) => ({ ...r, [key]: patch }))
  const run = useRef(0)
  const go = () => {
    const id = ++run.current
    setResults({})
    void (async () => {
      for (const album of albums) {
        if (run.current !== id) return
        set(album.rating_key, { busy: true })
        try {
          const value = await work(album)
          if (run.current !== id) return
          set(album.rating_key, { value })
        } catch (e) {
          if (run.current !== id) return
          set(album.rating_key, { error: errorMessage(e) })
        }
      }
    })()
  }
  useEffect(() => {
    if (start) go()
    const runs = run // the counter itself (not a DOM node): bumping it stops a run
    return () => {
      runs.current++
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the dialog opens
  }, [])
  return { results, go }
}

/** What happened to each album when its edit was written. */
function useWrites(onWritten?: (key: number) => void) {
  const [writes, setWrites] = useState<Record<number, 'writing' | 'written' | string>>({})
  const write = async (key: number, request: () => Promise<unknown>) => {
    setWrites((w) => ({ ...w, [key]: 'writing' }))
    try {
      await request()
      setWrites((w) => ({ ...w, [key]: 'written' }))
      onWritten?.(key)
    } catch (e) {
      setWrites((w) => ({ ...w, [key]: errorMessage(e) }))
    }
  }
  return { writes, write }
}

function WriteState({ state }: { state: 'writing' | 'written' | string | undefined }) {
  if (state === undefined) return null
  if (state === 'writing') return <Spinner small />
  if (state === 'written') return <Badge tone="ok">Queued for writing</Badge>
  return <span className="text-xs text-danger">{state}</span>
}

function BulkMusicBrainz({
  serverId,
  albums,
  canWrite,
  onWritten,
  onClose,
}: {
  serverId: number
  albums: AlbumIssues[]
  canWrite: boolean
  onWritten?: (key: number) => void
  onClose: () => void
}) {
  const { results } = useEach<MbBest>(albums, (a) => bulk.best(serverId, a.rating_key))
  const { writes, write } = useWrites(onWritten)
  const [skipped, setSkipped] = useState<Set<number>>(new Set())
  const refresh = useRefreshFiles(serverId)
  const accept = (a: AlbumIssues, best: MbBest) => write(a.rating_key, () => bulk.applyRelease(serverId, a.rating_key, best.release!.id))
  const clear = albums.filter((a) => {
    const best = results[a.rating_key]?.value
    return best?.clear && best.changes > 0 && !writes[a.rating_key] && !skipped.has(a.rating_key)
  })
  const looking = albums.some((a) => !results[a.rating_key] || results[a.rating_key].busy)
  const [acceptingAll, setAcceptingAll] = useState(false)

  return (
    <Dialog
      title="MusicBrainz releases"
      subtitle="The most likely release for each album: from the release ID already in its files, from identifying the songs by sound, or by searching. Clear matches (every file matched, the same number of tracks, a near-certain find) can be accepted together."
      onClose={() => {
        void refresh()
        onClose()
      }}
    >
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            disabled={!canWrite || clear.length === 0}
            busy={acceptingAll}
            onClick={async () => {
              setAcceptingAll(true)
              for (const a of clear) await accept(a, results[a.rating_key]!.value!)
              setAcceptingAll(false)
            }}
          >
            Accept {clear.length || ''} clear {clear.length === 1 ? 'match' : 'matches'}
          </Button>
          {looking && (
            <span className="flex items-center gap-2 text-xs text-muted">
              <Spinner small /> Looking them up, one a second (MusicBrainz's limit)…
            </span>
          )}
        </div>
        <ul className="max-h-[60dvh] divide-y divide-border overflow-y-auto">
          {albums.map((a) => {
            const r = results[a.rating_key]
            const best = r?.value
            const rel = best?.release
            return (
              <li key={a.rating_key} className="space-y-1 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <Link to={`/library/albums/${a.rating_key}`} className="min-w-0 truncate font-medium hover:underline">
                    {a.title} <span className="font-normal text-muted">· {a.artist_title}</span>
                  </Link>
                  <WriteState state={writes[a.rating_key]} />
                </div>
                {!r || r.busy ? (
                  <p className="text-xs text-muted">{r?.busy ? 'Looking…' : 'Waiting…'}</p>
                ) : r.error ? (
                  <p className="text-xs text-danger">{r.error}</p>
                ) : !rel ? (
                  <p className="text-xs text-muted">{best?.note ?? 'Nothing found.'}</p>
                ) : (
                  <>
                    <p className="text-xs">
                      <a href={`https://musicbrainz.org/release/${rel.id}`} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                        {rel.title}
                      </a>{' '}
                      by {rel.artist} · {[rel.date?.slice(0, 4), rel.country, rel.formats, rel.label].filter(Boolean).join(', ')}
                    </p>
                    <p className="text-xs text-muted">
                      {best.source === 'tags'
                        ? 'The release ID in the files'
                        : best.source === 'sound'
                          ? `Identified by sound (${rel.score}% of the files)`
                          : `Search score ${rel.score}`}
                      {' · '}
                      {best.matched} of {best.files} files matched · {best.changes} {best.changes === 1 ? 'change' : 'changes'}
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      {best.changes === 0 ? (
                        <Badge tone="ok">The files already match</Badge>
                      ) : best.clear ? (
                        <Badge tone="ok">Clear match</Badge>
                      ) : (
                        <span className="text-xs text-warn">{best.note}</span>
                      )}
                      {best.changes > 0 && !writes[a.rating_key] && !skipped.has(a.rating_key) && (
                        <>
                          <Button variant="secondary" size="sm" disabled={!canWrite} onClick={() => void accept(a, best)}>
                            Accept
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => setSkipped((s) => new Set(s).add(a.rating_key))}>
                            Skip
                          </Button>
                        </>
                      )}
                      {skipped.has(a.rating_key) && <span className="text-xs text-muted">Skipped</span>}
                      <Link to={`/library/albums/${a.rating_key}`} className="text-xs text-muted hover:text-text">
                        Pick another on the album page →
                      </Link>
                    </div>
                  </>
                )}
              </li>
            )
          })}
        </ul>
      </div>
    </Dialog>
  )
}

function BulkPlex({
  serverId,
  albums,
  canWrite,
  onWritten,
  onClose,
}: {
  serverId: number
  albums: AlbumIssues[]
  canWrite: boolean
  onWritten?: (key: number) => void
  onClose: () => void
}) {
  const { results } = useEach(albums, (a) => bulk.plexPreview(serverId, a.rating_key))
  const { writes, write } = useWrites(onWritten)
  const refresh = useRefreshFiles(serverId)
  const [writing, setWriting] = useState(false)
  const todo = albums.filter((a) => (results[a.rating_key]?.value?.changes ?? 0) > 0 && !writes[a.rating_key])
  const looking = albums.some((a) => !results[a.rating_key] || results[a.rating_key].busy)

  return (
    <Dialog
      title="Write Plex's metadata into the files"
      subtitle="Titles, artists, album, album artist, track and disc numbers, year and genres as Plex has them. Each album is written as its own edit."
      onClose={() => {
        void refresh()
        onClose()
      }}
    >
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            disabled={!canWrite || todo.length === 0 || looking}
            busy={writing}
            onClick={async () => {
              setWriting(true)
              for (const a of todo) await write(a.rating_key, () => bulk.plexWrite(serverId, a.rating_key))
              setWriting(false)
            }}
          >
            Write to {todo.length || ''} {todo.length === 1 ? 'album' : 'albums'}
          </Button>
          {looking && <Spinner small />}
        </div>
        <ul className="max-h-[60dvh] divide-y divide-border overflow-y-auto">
          {albums.map((a) => {
            const r = results[a.rating_key]
            const preview = r?.value
            return (
              <li key={a.rating_key} className="flex items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <p className="truncate font-medium">
                    {a.title} <span className="font-normal text-muted">· {a.artist_title}</span>
                  </p>
                  <p className="text-xs text-muted">
                    {!r || r.busy
                      ? 'Comparing…'
                      : r.error
                        ? r.error
                        : preview!.changes === 0
                          ? 'The files already match Plex.'
                          : `${preview!.changes} ${preview!.changes === 1 ? 'change' : 'changes'} in ${preview!.files} ${preview!.files === 1 ? 'file' : 'files'}: ${Object.keys(
                              preview!.by_field,
                            )
                              .map((f) => FIELD_NAMES[f] ?? f)
                              .join(', ')}`}
                  </p>
                </div>
                <WriteState state={writes[a.rating_key]} />
              </li>
            )
          })}
        </ul>
      </div>
    </Dialog>
  )
}

async function waitForPlan(serverId: number, jobId: number, stopped: () => boolean): Promise<RenamePlan> {
  for (;;) {
    const plan = await bulk.renamePlan(serverId, jobId)
    if (plan.job.status === 'succeeded' || plan.job.status === 'failed' || stopped()) return plan
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
}

function BulkRename({
  serverId,
  albums,
  canWrite,
  onClose,
}: {
  serverId: number
  albums: AlbumIssues[]
  canWrite: boolean
  onClose: () => void
}) {
  const [template, setTemplate] = useState(storedTemplate)
  const closed = useRef(false)
  useEffect(
    () => () => {
      closed.current = true
    },
    [],
  )
  const { results, go } = useEach(
    albums,
    async (a) => {
      const job = await bulk.planRenames(serverId, a.rating_key, template.trim())
      return waitForPlan(serverId, job.id, () => closed.current)
    },
    false,
  )
  const { writes, write } = useWrites()
  const refresh = useRefreshFiles(serverId)
  const [moving, setMoving] = useState(false)
  const checked = Object.keys(results).length > 0
  const looking = checked && albums.some((a) => !results[a.rating_key] || results[a.rating_key].busy)
  const todo = albums.filter((a) => {
    const plan = results[a.rating_key]?.value
    return plan?.job.status === 'succeeded' && plan.moves.length > 0 && !writes[a.rating_key]
  })

  const check = (e: FormEvent) => {
    e.preventDefault()
    storeTemplate(template.trim())
    go()
  }

  return (
    <Dialog
      title="Rename the albums' files"
      subtitle="Into folders and names from a template (the same one as on album pages). Nothing moves until you've seen where everything goes; each album's moves are their own edit, undone from its page."
      onClose={() => {
        void refresh()
        onClose()
      }}
    >
      <div className="space-y-3 text-sm">
        <form onSubmit={check} className="flex flex-wrap gap-2">
          <input
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
            aria-label="Template"
            spellCheck={false}
            autoComplete="off"
            className="min-w-0 flex-1 basis-72 rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-sm"
          />
          <Button type="submit" variant="secondary" busy={looking} disabled={!template.trim()}>
            Check where everything goes
          </Button>
          {template !== DEFAULT_TEMPLATE && (
            <Button variant="ghost" onClick={() => setTemplate(DEFAULT_TEMPLATE)}>
              Plex's layout
            </Button>
          )}
        </form>
        {checked && (
          <>
            <Button
              variant="primary"
              disabled={!canWrite || todo.length === 0 || looking}
              busy={moving}
              onClick={async () => {
                setMoving(true)
                for (const a of todo) await write(a.rating_key, () => bulk.applyRenames(serverId, results[a.rating_key]!.value!.job.id))
                setMoving(false)
              }}
            >
              Move the files of {todo.length || ''} {todo.length === 1 ? 'album' : 'albums'}
            </Button>
            <ul className="max-h-[55dvh] divide-y divide-border overflow-y-auto">
              {albums.map((a) => {
                const r = results[a.rating_key]
                const plan = r?.value
                return (
                  <li key={a.rating_key} className="space-y-1 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <p className="min-w-0 truncate font-medium">
                        {a.title} <span className="font-normal text-muted">· {a.artist_title}</span>
                      </p>
                      <WriteState state={writes[a.rating_key]} />
                    </div>
                    <p className="text-xs text-muted">
                      {!r || r.busy
                        ? 'Working out the moves…'
                        : r.error
                          ? r.error
                          : plan!.job.status === 'failed'
                            ? plan!.job.error
                            : plan!.moves.length === 0
                              ? 'Already where the template puts them.'
                              : `${plan!.moves.length} to move${plan!.skipped.length ? `, ${plan!.skipped.length} can't` : ''}`}
                    </p>
                    {plan && plan.moves.length > 0 && (
                      <details className="text-xs">
                        <summary className="cursor-pointer text-muted hover:text-text">Where they go</summary>
                        <ul className="mt-1 space-y-0.5 font-mono">
                          {plan.moves.slice(0, 30).map((m) => (
                            <li key={m.source} className="break-all">
                              {m.target}
                            </li>
                          ))}
                          {plan.moves.length > 30 && <li className="text-muted">…and {plan.moves.length - 30} more</li>}
                        </ul>
                      </details>
                    )}
                  </li>
                )
              })}
            </ul>
          </>
        )}
      </div>
    </Dialog>
  )
}
