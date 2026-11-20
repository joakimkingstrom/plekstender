import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../api/client'
import { useLeaderboard, type GameMode, type LeaderboardRow } from '../api/game'
import { Segmented } from '../components/Segmented'
import { Avatar, Badge, Card, Notice, Spinner } from '../components/ui'
import { formatCount, formatRelative } from '../format'

const MODES: [GameMode | 'all', string][] = [
  ['all', 'All modes'],
  ['track', 'Songs'],
  ['album', 'Albums'],
  ['artist', 'Artists'],
]
const MEDALS = ['🥇', '🥈', '🥉']

/** Guess the intro: everyone's scores on this Plekstender, the most points first. */
export function ScoresPage() {
  const [mode, setMode] = useState<GameMode | 'all'>('all')
  const board = useLeaderboard(mode === 'all' ? null : mode)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented label="Mode" value={mode} options={MODES} onChange={setMode} />
        <p className="text-sm text-muted">
          Guess the intro, on{' '}
          <Link to="/library/rate" className="text-accent underline">
            Rate
          </Link>{' '}
          (card view).
        </p>
      </div>
      {board.isPending ? (
        <Spinner />
      ) : board.isError ? (
        <Notice tone="error">{errorMessage(board.error)}</Notice>
      ) : board.data.length === 0 ? (
        <Card>
          <p className="text-sm text-muted">
            No scores yet{mode === 'all' ? '' : ' in this mode'}. Turn on Guess the intro under Options on the Rate page
            (card view) to play.
          </p>
        </Card>
      ) : (
        <div className={board.isPlaceholderData ? 'opacity-70' : ''}>
          {/* A table on wider screens, cards on phones. */}
          <div className="hidden overflow-hidden rounded-2xl border border-border bg-surface md:block">
            <table className="w-full text-sm">
              <thead className="border-b border-border text-left text-xs uppercase tracking-wider text-muted">
                <tr>
                  <th className="w-12 px-4 py-3 font-semibold">#</th>
                  <th className="px-4 py-3 font-semibold">Player</th>
                  <th className="px-4 py-3 text-right font-semibold">Points</th>
                  <th className="px-4 py-3 text-right font-semibold">Best streak</th>
                  <th className="px-4 py-3 text-right font-semibold">Latest game</th>
                  <th className="px-4 py-3 text-right font-semibold">Guessed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {board.data.map((row, i) => (
                  <tr key={row.user_id} className={row.you ? 'bg-accent/5' : ''}>
                    <td className="px-4 py-3 text-muted tabular-nums">{MEDALS[i] ?? i + 1}</td>
                    <td className="px-4 py-3">
                      <Player row={row} />
                    </td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums">{formatCount(row.points)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{formatCount(row.best_streak)}</td>
                    <td className="px-4 py-3 text-right">
                      <Latest row={row} />
                    </td>
                    <td className="px-4 py-3 text-right text-muted tabular-nums">{accuracy(row)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ol className="space-y-2 md:hidden">
            {board.data.map((row, i) => (
              <li key={row.user_id} className={`rounded-2xl border border-border p-3 ${row.you ? 'bg-accent/5' : 'bg-surface'}`}>
                <div className="flex items-center gap-3">
                  <span className="w-7 text-center text-sm text-muted tabular-nums">{MEDALS[i] ?? i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <Player row={row} />
                  </div>
                  <span className="font-display text-lg font-bold tabular-nums">{formatCount(row.points)}</span>
                </div>
                <dl className="mt-2 grid grid-cols-3 gap-2 pl-10 text-xs">
                  <Stat label="Best streak" value={formatCount(row.best_streak)} />
                  <Stat label="Latest game" value={<Latest row={row} />} />
                  <Stat label="Guessed" value={accuracy(row)} />
                </dl>
              </li>
            ))}
          </ol>
          <p className="mt-2 text-xs text-muted">
            Points: the seconds left when you guess right, plus 3 for the right artist (songs and albums). A game is the
            rounds played without a 30-minute break. Latest games count from when scores started being kept.
          </p>
        </div>
      )}
    </div>
  )
}

function accuracy(row: LeaderboardRow): string {
  if (!row.rounds) return '–'
  return `${Math.round((row.correct / row.rounds) * 100)}% of ${formatCount(row.rounds)}`
}

function Player({ row }: { row: LeaderboardRow }) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <Avatar url={row.thumb_url} name={row.username} />
      <span className="truncate font-medium">{row.username}</span>
      {row.you && <Badge tone="accent">You</Badge>}
    </span>
  )
}

function Latest({ row }: { row: LeaderboardRow }) {
  if (row.last_score === null || row.last_score === undefined) return <span className="text-muted">–</span>
  return (
    <span className="inline-flex flex-col items-end">
      <span className="tabular-nums">
        {formatCount(row.last_score)} <span className="text-muted">in {row.last_rounds} {row.last_rounds === 1 ? 'round' : 'rounds'}</span>
      </span>
      {row.last_played_at && <span className="text-xs text-muted">{formatRelative(row.last_played_at)}</span>}
    </span>
  )
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="[&>span]:items-start">{value}</dd>
    </div>
  )
}
