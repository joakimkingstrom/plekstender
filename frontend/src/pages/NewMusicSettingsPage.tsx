import { Link, Navigate } from 'react-router'
import { errorMessage } from '../api/client'
import { useActiveServer } from '../api/library'
import { useNewMusic, useSetNewMusic, type NewMusic, type NewMusicChoice } from '../api/newMusic'
import { Card, Notice, Spinner } from '../components/ui'
import { formatRelative } from '../format'

type Write = NonNullable<NewMusicChoice['lyrics_write']>
const NO_WRITE: Write = { sidecar: false, lyricsfile: false, embedded: false, overwrite: false }
const WRITES: [keyof Write, string][] = [
  ['sidecar', 'A .lrc (synced) or .txt next to each song'],
  ['lyricsfile', 'A .lyricsfile.yaml next to each song'],
  ['embedded', 'In each song’s tags (plain lyrics)'],
  ['overwrite', 'Replace lyrics the files already have'],
]

/** Settings → New music (server owners): what's done when new artists and albums arrive in
 * Plex, after each library sync. */
export function NewMusicSettingsPage() {
  const { active, servers } = useActiveServer()
  if (servers.isPending) return <Spinner />
  if (!active?.is_owner) return <Navigate to="/settings" replace />
  return (
    <div className="max-w-2xl">
      <NewMusicCard serverId={active.id} />
    </div>
  )
}

function NewMusicCard({ serverId }: { serverId: number }) {
  const found = useNewMusic(serverId)
  const save = useSetNewMusic(serverId)
  if (found.isPending) return <Spinner />
  if (found.isError) return <Notice tone="error">{errorMessage(found.error)}</Notice>
  const now = found.data
  const choice: NewMusicChoice = { logos: now.logos, covers: now.covers, lyrics: now.lyrics, lyrics_write: now.lyrics_write ?? null }
  const set = (change: Partial<NewMusicChoice>) => save.mutate({ ...choice, ...change })
  const write = choice.lyrics_write ?? NO_WRITE
  return (
    <Card title="New music">
      <div className="space-y-4 text-sm">
        <p className="text-muted">
          After each library sync, Plekstender can do these for artists and albums new in Plex. Only what’s added from the
          moment you turn one on counts as new; you’re told what was done, and the jobs show on{' '}
          <Link to="/settings/jobs" className="underline hover:text-text">
            Jobs
          </Link>
          .
        </p>
        <fieldset className="space-y-3">
          <legend className="sr-only">For new music</legend>
          <Choice
            label="Logos for new artists"
            hint="fanart.tv’s most liked logo, as Plex’s artist image."
            checked={choice.logos}
            onChange={(logos) => set({ logos })}
            problem={!now.fanart_ready ? 'Needs fanart.tv: an admin can add its key on Settings → Library tuning.' : null}
          />
          <Choice
            label="Best covers for new albums"
            hint="The best match from your cover sources (as Apply best match does), when it’s at least 1000 × 1000."
            checked={choice.covers}
            onChange={(covers) => set({ covers })}
          />
          <Choice
            label="Lyrics for new albums"
            hint="Looked up on LRCLIB for every new song."
            checked={choice.lyrics}
            onChange={(lyrics) => set({ lyrics, lyrics_write: lyrics && !choice.lyrics_write ? { ...NO_WRITE, sidecar: true } : choice.lyrics_write })}
            problem={!now.folder_ready ? 'Needs the music folder: set it up on Settings → Music files.' : null}
          />
          {choice.lyrics && (
            <fieldset className="ml-6 space-y-1.5">
              <legend className="mb-1 text-xs text-muted">Write what’s found (none: only look them up)</legend>
              {WRITES.map(([k, label]) => (
                <label key={k} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={write[k]} onChange={(e) => set({ lyrics_write: { ...write, [k]: e.target.checked } })} />
                  {label}
                </label>
              ))}
              {!now.tag_editing && <p className="text-xs text-warn">Tag editing is off for this server: lyrics are only looked up until it’s on.</p>}
            </fieldset>
          )}
        </fieldset>
        {save.isError && <Notice tone="error">{errorMessage(save.error)}</Notice>}
        <Since now={now} />
      </div>
    </Card>
  )
}

function Choice({ label, hint, checked, onChange, problem = null }: { label: string; hint: string; checked: boolean; onChange: (on: boolean) => void; problem?: string | null }) {
  return (
    <label className="flex items-start gap-2">
      <input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="font-medium">{label}</span>
        <span className="block text-xs text-muted">{hint}</span>
        {problem && checked && <span className="block text-xs text-warn">{problem}</span>}
      </span>
    </label>
  )
}

function Since({ now }: { now: NewMusic }) {
  if (!now.since) return null
  return <p className="text-xs text-muted">Counting music added to Plex since {formatRelative(now.since)}.</p>
}
