import { useState } from 'react'
import { errorMessage } from '../api/client'
import {
  useCreatePlaylistImport,
  usePreviewPlaylistImport,
  usePreviewLinkImport,
  type PlaylistImportPreview,
} from '../api/playlists'
import { formatDuration } from '../format'
import { FilePicker } from './controls'
import { Button, Card, Notice } from './ui'

/** Bring a playlist in from Spotify, Apple Music, Tidal… (their exports) as a Plex playlist. */
export function PlaylistImport({ serverId }: { serverId: number }) {
  const preview = usePreviewPlaylistImport(serverId)
  const fromLink = usePreviewLinkImport(serverId)
  const [link, setLink] = useState('')
  // The last one read (a file or a link), shown for review.
  const latest = (preview.submittedAt ?? 0) >= (fromLink.submittedAt ?? 0) ? preview : fromLink
  return (
    <Card title="Import a playlist">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          From a CSV (e.g. Exportify or TuneMyMusic, for Spotify, Apple Music, Tidal and more), an M3U or a JSPF file. Its
          songs are matched to your library; you choose which go in, and it’s made in Plex under your account. Or paste a
          public Spotify or Tidal playlist’s link.
        </p>
        <FilePicker
          accept=".csv,.m3u,.m3u8,.jspf,.json,text/csv,audio/x-mpegurl,application/json"
          label="A playlist file"
          onFile={(file) => preview.mutate(file)}
        />
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (link.trim()) fromLink.mutate(link.trim())
          }}
        >
          <label className="min-w-0 flex-1 space-y-1">
            <span className="block font-medium">Spotify or Tidal playlist link</span>
            <input
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder="https://open.spotify.com/playlist/… or https://tidal.com/playlist/…"
              className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2"
            />
          </label>
          <Button type="submit" disabled={!link.trim()} busy={fromLink.isPending}>
            Read it
          </Button>
        </form>
        {latest.isPending && <p className="text-muted">Matching the songs…</p>}
        {latest.isError && <Notice tone="error">{errorMessage(latest.error)}</Notice>}
        {latest.data && <Review key={latest.submittedAt} serverId={serverId} data={latest.data} />}
      </div>
    </Card>
  )
}

function Review({ serverId, data }: { serverId: number; data: PlaylistImportPreview }) {
  const create = useCreatePlaylistImport(serverId)
  const [name, setName] = useState(data.name ?? 'Imported playlist')
  const found = data.songs.filter((s) => s.match)
  const missing = data.songs.filter((s) => !s.match)
  const [chosen, setChosen] = useState<Set<number>>(() => new Set(found.map((s) => s.index)))
  const keys = found.filter((s) => chosen.has(s.index)).map((s) => s.match!.rating_key)
  if (create.data)
    return (
      <Notice>
        Made “{name}” in Plex with {create.data.songs} {create.data.songs === 1 ? 'song' : 'songs'}.
      </Notice>
    )
  return (
    <div className="space-y-3">
      <p>
        <strong>{data.matched}</strong> of {data.total} songs are in your library.
      </p>
      {found.length > 0 && (
        <ul className="max-h-80 divide-y divide-border overflow-y-auto rounded-lg border border-border" aria-label="Songs found">
          {found.map((s) => {
            const m = s.match!
            return (
              <li key={s.index} className="flex items-center gap-3 px-3 py-1.5">
                <input
                  type="checkbox"
                  checked={chosen.has(s.index)}
                  aria-label={`Include ${s.title}`}
                  onChange={(e) => {
                    const next = new Set(chosen)
                    if (e.target.checked) next.add(s.index)
                    else next.delete(s.index)
                    setChosen(next)
                  }}
                />
                <span className="min-w-0 flex-1 truncate">
                  {m.title}
                  <span className="text-muted">
                    {' '}
                    · {m.artist}
                    {m.album ? ` · ${m.album}` : ''}
                  </span>
                </span>
                {m.how === 'title' && (
                  <span className="rounded-full bg-warn/15 px-2 text-xs text-warn" title={`The file says ${s.artist}`}>
                    likely
                  </span>
                )}
                <span className="text-xs text-muted tabular-nums">{formatDuration(m.duration_ms)}</span>
              </li>
            )
          })}
        </ul>
      )}
      {missing.length > 0 && (
        <details>
          <summary className="cursor-pointer text-muted">Not in your library ({missing.length})</summary>
          <ul className="mt-1 list-disc pl-5 text-xs text-muted">
            {missing.map((s) => (
              <li key={s.index}>
                {s.artist} – {s.title}
                {s.album ? ` (${s.album})` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (keys.length && name.trim()) create.mutate({ name: name.trim(), rating_keys: keys })
        }}
      >
        <label className="min-w-0 flex-1 space-y-1">
          <span className="block font-medium">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2" />
        </label>
        <Button type="submit" variant="primary" disabled={!keys.length || !name.trim()} busy={create.isPending}>
          Make it in Plex ({keys.length} {keys.length === 1 ? 'song' : 'songs'})
        </Button>
      </form>
      {create.isError && <Notice tone="error">{errorMessage(create.error)}</Notice>}
    </div>
  )
}
