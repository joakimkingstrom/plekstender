import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useFilesSummary, useLatestLyricsLookup, useLyricsCoverage } from '../api/files'
import { Donut, type DonutSlice } from '../components/charts'
import { LyricsPanel } from '../components/LyricsDialog'
import { Card, Notice, Spinner } from '../components/ui'
import { useLibraryServer } from '../components/useLibraryServer'
import { formatCount, formatShare } from '../format'

function songs(n: number) {
  return `${formatCount(n)} ${n === 1 ? 'song' : 'songs'}`
}

/** Library → Lyrics (server owners): how much of the library has lyrics, and getting more. */
export function LyricsPage() {
  const { active, blocker } = useLibraryServer()
  if (blocker) return blocker
  if (!active!.is_owner) return <Notice>Only the server's owner can add lyrics to its music files.</Notice>
  return <Lyrics serverId={active!.id} />
}

function Lyrics({ serverId }: { serverId: number }) {
  const summary = useFilesSummary(serverId)
  const latest = useLatestLyricsLookup(serverId)
  if (summary.isPending || latest.isPending) return <Spinner />
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
  return (
    <div className="space-y-4">
      <Coverage serverId={serverId} />
      <Card title="Get lyrics">
        <p className="mb-4 text-sm text-muted">
          From LRCLIB, a free and open collection of lyrics, for every song in the library. Nothing is written until you
          say so, and the edit can be undone. For a few albums, pick them on{' '}
          <Link to="/library/tag-editing" className="text-accent hover:underline">
            Tag editing
          </Link>
          .
        </p>
        <LyricsPanel
          key={latest.data?.id ?? 'new'}
          serverId={serverId}
          albums={[]}
          canWrite={summary.data.tag_editing}
          jobId={latest.data?.id ?? null}
        />
      </Card>
    </div>
  )
}

function Coverage({ serverId }: { serverId: number }) {
  const coverage = useLyricsCoverage(serverId)
  if (coverage.isPending) return <Spinner />
  if (coverage.isError) return <Notice tone="error">{errorMessage(coverage.error)}</Notice>
  const c = coverage.data
  const checked = c.files - c.unchecked
  const withLyrics = c.word + c.synced + c.plain
  const slices: DonutSlice[] = [
    { key: 'word', label: 'Word-synced', value: c.word, color: 'var(--color-lyrics-word)', note: 'Each word timed (enhanced LRC or a Lyricsfile)' },
    { key: 'synced', label: 'Synced', value: c.synced, color: 'var(--color-lyrics-synced)', note: 'Each line timed' },
    { key: 'plain', label: 'Plain', value: c.plain, color: 'var(--color-lyrics-plain)', note: 'No times' },
    ...(c.instrumental > 0
      ? [{ key: 'instrumental', label: 'Instrumental', value: c.instrumental, color: 'var(--color-lyrics-instrumental)' }]
      : []),
    { key: 'none', label: 'No lyrics', value: c.none, color: 'var(--color-lyrics-none)' },
  ]
  return (
    <Card title="Lyrics in your library">
      <Donut
        slices={slices}
        caption="Songs by kind of lyrics"
        unit={songs}
        center={{ value: formatShare(withLyrics, checked), label: 'have lyrics' }}
      />
      <p className="mt-4 text-xs text-muted">
        Lyrics next to each song (.lrc, .txt or .lyricsfile.yaml) or in its tags, as of the last scan of the music folder;
        a song counts by the best it has.
        {c.unchecked > 0 &&
          ` ${songs(c.unchecked)} haven't been looked at since lyrics were counted: they're read at the next scan.`}
      </p>
    </Card>
  )
}
