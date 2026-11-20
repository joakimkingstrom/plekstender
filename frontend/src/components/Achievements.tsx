import { useState } from 'react'
import { GROUP_SYMBOL, useAchievements, useNewAchievements, useSeenAchievements, type Achievement } from '../api/achievements'
import { formatCount, formatDate } from '../format'
import { Button, Spinner } from './ui'

/** A round badge, like a record label: the accent colour once unlocked, a silhouette before. */
function Medal({ a, size = 'size-14' }: { a: Achievement; size?: string }) {
  const on = Boolean(a.unlocked_at)
  return (
    <span
      className={`${size} relative flex shrink-0 items-center justify-center rounded-full font-display text-2xl ${
        on ? 'bg-accent text-on-accent shadow-md' : 'bg-surface-2 text-muted/50'
      }`}
      aria-hidden
    >
      <span className={`absolute inset-[18%] rounded-full border ${on ? 'border-on-accent/30' : 'border-border'}`} />
      {GROUP_SYMBOL[a.group]}
    </span>
  )
}

/** Profile → Achievements: unlocked ones with their date and message, locked ones as
 * silhouettes with a hint and progress. */
export function AchievementList() {
  const achievements = useAchievements()
  if (achievements.isPending) return <Spinner />
  if (!achievements.data?.enabled) return null
  const items = achievements.data.items
  const unlocked = items.filter((a) => a.unlocked_at).sort((a, b) => (b.unlocked_at ?? '').localeCompare(a.unlocked_at ?? ''))
  const locked = items.filter((a) => !a.unlocked_at).sort((a, b) => b.progress / b.goal - a.progress / a.goal)
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        {formatCount(unlocked.length)} of {formatCount(items.length)} unlocked.
      </p>
      <ul className="grid gap-3 sm:grid-cols-2">
        {unlocked.map((a) => (
          <li key={a.id} className="flex gap-3 rounded-xl border border-border bg-surface p-3">
            <Medal a={a} />
            <div className="min-w-0">
              <p className="font-semibold">{a.name}</p>
              <p className="text-sm">{a.message}</p>
              <p className="mt-1 text-xs text-muted">
                {a.hint} · {formatDate(a.unlocked_at!)}
              </p>
            </div>
          </li>
        ))}
        {locked.map((a) => (
          <li key={a.id} className="flex gap-3 rounded-xl border border-dashed border-border p-3">
            <Medal a={a} />
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-muted">???</p>
              <p className="text-sm text-muted">{a.hint}</p>
              <div className="mt-2 h-1.5 rounded-full bg-surface-2" role="progressbar" aria-valuemin={0} aria-valuemax={a.goal} aria-valuenow={Math.min(a.progress, a.goal)} aria-label={`${a.hint}: ${formatCount(a.progress)} of ${formatCount(a.goal)}`}>
                <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, (a.progress / a.goal) * 100)}%` }} />
              </div>
              <p className="mt-1 text-xs text-muted tabular-nums">
                {formatCount(Math.min(a.progress, a.goal))} / {formatCount(a.goal)}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

const CONFETTI = Array.from({ length: 18 }, (_, i) => i)

/** A short celebration when one unlocks (calmer with reduced motion): a spinning record,
 * a little confetti, the name and its message. */
export function AchievementCelebration({ enabled }: { enabled: boolean }) {
  const fresh = useNewAchievements(enabled)
  const seen = useSeenAchievements()
  const [index, setIndex] = useState(0)
  const items = fresh.data?.enabled ? fresh.data.items : []
  if (!items.length) return null
  const a = items[Math.min(index, items.length - 1)]
  const close = () => {
    if (index + 1 < items.length) setIndex(index + 1)
    else {
      seen.mutate(items.map((x) => x.id))
      setIndex(0)
    }
  }
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-24 z-40 flex justify-center px-4 md:bottom-8">
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-auto relative w-full max-w-sm overflow-hidden rounded-2xl border border-border bg-surface p-4 shadow-2xl motion-safe:animate-[rewind-in_400ms_ease-out]"
      >
        <div className="pointer-events-none absolute inset-0 hidden motion-safe:block" aria-hidden>
          {CONFETTI.map((i) => (
            <span
              key={i}
              className="absolute top-0 size-1.5 rounded-sm motion-safe:animate-[confetti_1.6s_ease-in_forwards]"
              style={{
                left: `${(i * 37) % 100}%`,
                background: ['var(--color-accent)', 'var(--color-chart)', 'var(--color-ok)', 'var(--color-warn)'][i % 4],
                animationDelay: `${(i % 6) * 80}ms`,
              }}
            />
          ))}
        </div>
        <div className="relative flex items-center gap-4">
          <span className="motion-safe:animate-[spin_3s_linear_infinite]">
            <Medal a={a} size="size-16" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-wider text-accent">Achievement unlocked</p>
            <p className="font-display text-lg font-bold">{a.name}</p>
            <p className="text-sm">{a.message}</p>
          </div>
        </div>
        <div className="relative mt-3 flex items-center justify-between">
          <span className="text-xs text-muted">{items.length > 1 ? `${index + 1} of ${items.length}` : a.hint}</span>
          <Button variant="primary" onClick={close} className="px-3 py-1.5 text-sm">
            {index + 1 < items.length ? 'Next' : 'Nice!'}
          </Button>
        </div>
      </div>
    </div>
  )
}
