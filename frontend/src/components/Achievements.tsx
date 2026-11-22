import { useState, type CSSProperties } from 'react'
import { GROUP_SYMBOL, useAchievements, useNewAchievements, useSeenAchievements, type Achievement } from '../api/achievements'
import { formatCount, formatDate } from '../format'
import { Pile } from './Pile'
import { Button, Spinner } from './ui'

/** Each kind of card has its own colour, like the sets in a trading card game. */
const GROUP: Record<Achievement['group'], { label: string; colour: string }> = {
  library: { label: 'Library', colour: '#4f7cff' },
  rating: { label: 'Rating', colour: '#e8a23a' },
  game: { label: 'Guess the intro', colour: '#a95cf0' },
  gigs: { label: 'Gigs', colour: '#e5505e' },
  connections: { label: 'Connections', colour: '#25a99d' },
  listening: { label: 'Listening', colour: '#34b77f' },
}

/** One achievement as a trading card: name, artwork (a record in the set's colour), its
 * flavour text, what it was for and when. */
export function AchievementCard({ a, number, total }: { a: Achievement; number: number; total: number }) {
  const { label, colour } = GROUP[a.group]
  const frame: CSSProperties = {
    background: `linear-gradient(145deg, ${colour}, color-mix(in oklab, ${colour} 55%, black))`,
  }
  const art: CSSProperties = {
    background: `radial-gradient(circle at 50% 45%, color-mix(in oklab, ${colour} 45%, white) 0%, ${colour} 45%, color-mix(in oklab, ${colour} 40%, black) 100%)`,
  }
  return (
    <article className="flex aspect-[5/7] w-64 select-none flex-col rounded-2xl p-2 shadow-xl" style={frame} aria-label={a.name}>
      <div className="flex min-h-0 flex-1 flex-col rounded-xl bg-surface p-2.5 text-text">
        <header className="flex items-baseline justify-between gap-2">
          <h3 className="truncate font-display text-base font-bold leading-tight">{a.name}</h3>
          <span className="text-lg leading-none" style={{ color: colour }} aria-hidden>
            {GROUP_SYMBOL[a.group]}
          </span>
        </header>
        <div className="relative mt-1.5 flex aspect-[16/10] items-center justify-center overflow-hidden rounded-lg" style={art} aria-hidden>
          {/* A record: grooves, a label in the set's colour and the kind's symbol. */}
          <span className="relative flex size-24 items-center justify-center rounded-full bg-[#141417] shadow-lg">
            <span className="absolute inset-[10%] rounded-full border border-white/10" />
            <span className="absolute inset-[20%] rounded-full border border-white/10" />
            <span className="absolute inset-[29%] rounded-full border border-white/5" />
            <span className="relative flex size-9 items-center justify-center rounded-full text-lg text-white" style={{ background: colour }}>
              {GROUP_SYMBOL[a.group]}
            </span>
          </span>
        </div>
        <p className="mt-1.5 border-b border-border pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">
          {label}
        </p>
        <p className="mt-1.5 min-h-0 flex-1 overflow-hidden text-xs italic leading-snug">{a.message}</p>
        <p className="mt-1 text-xs font-semibold leading-snug">✓ {a.hint}</p>
        <footer className="mt-1 flex justify-between text-[10px] text-muted tabular-nums">
          <span>{a.unlocked_at ? formatDate(a.unlocked_at) : ''}</span>
          <span>
            {String(number).padStart(2, '0')}/{total}
          </span>
        </footer>
      </div>
    </article>
  )
}

/** Profile → Achievements: the cards collected so far, and how many there are to collect. */
export function AchievementList() {
  const achievements = useAchievements()
  if (achievements.isPending) return <Spinner />
  if (!achievements.data?.enabled) return null
  const items = achievements.data.items
  const numbers = new Map(items.map((a, i) => [a.id, i + 1]))
  const collected = items
    .filter((a) => a.unlocked_at)
    .sort((a, b) => (b.unlocked_at ?? '').localeCompare(a.unlocked_at ?? ''))
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        You've collected{' '}
        <strong className="text-text">
          {formatCount(collected.length)} of {formatCount(items.length)}
        </strong>{' '}
        cards.
      </p>
      {collected.length ? (
        <Pile
          items={collected}
          itemKey={(a) => a.id}
          renderItem={(a) => <AchievementCard a={a} number={numbers.get(a.id) ?? 0} total={items.length} />}
          label="Your achievement cards"
          className="h-[25.5rem] w-full max-w-md"
        />
      ) : (
        <p className="text-sm text-muted">None yet: they turn up as you use Plekstender.</p>
      )}
    </div>
  )
}

const CONFETTI = Array.from({ length: 24 }, (_, i) => i)

/** The back of a card: what it shows while it flips over. */
function CardBack() {
  return (
    <div
      className="absolute inset-0 flex items-center justify-center rounded-2xl p-2 shadow-xl [backface-visibility:hidden] [transform:rotateY(180deg)]"
      style={{ background: 'linear-gradient(145deg, var(--color-accent), color-mix(in oklab, var(--color-accent) 45%, black))' }}
      aria-hidden
    >
      <div className="flex size-full items-center justify-center rounded-xl border-2 border-white/25 bg-[repeating-linear-gradient(45deg,transparent_0_10px,rgb(255_255_255/0.06)_10px_20px)]">
        <span className="flex size-28 items-center justify-center rounded-full bg-[#141417] shadow-lg">
          <span className="flex size-10 items-center justify-center rounded-full bg-accent font-display text-xl text-on-accent">♪</span>
        </span>
      </div>
    </div>
  )
}

/** When one unlocks: the new trading card flips over in front of the page (just fades in
 * with reduced motion), with a little confetti and a shine, then goes into the collection. */
export function AchievementCelebration({ enabled }: { enabled: boolean }) {
  const fresh = useNewAchievements(enabled)
  const all = useAchievements()
  const seen = useSeenAchievements()
  const [index, setIndex] = useState(0)
  const items = fresh.data?.enabled ? fresh.data.items : []
  const a = items.length ? items[Math.min(index, items.length - 1)] : null
  if (!a) return null
  const list = all.data?.items ?? []
  const number = list.findIndex((x) => x.id === a.id) + 1 || index + 1
  const total = Math.max(list.length, number)
  const close = () => {
    if (index + 1 < items.length) setIndex(index + 1)
    else {
      seen.mutate(items.map((x) => x.id))
      setIndex(0)
    }
  }
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Achievement unlocked"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-black/60 px-4 backdrop-blur-sm motion-safe:animate-[fade-in_250ms_ease-out]"
      onKeyDown={(e) => {
        if (e.key === 'Escape') close()
      }}
    >
      <div className="pointer-events-none absolute inset-x-0 top-[18%] mx-auto hidden h-0 max-w-sm motion-safe:block" aria-hidden>
        {CONFETTI.map((i) => (
          <span
            key={`${a.id}-${i}`}
            className="absolute top-0 size-2 rounded-sm motion-safe:animate-[confetti_1.8s_ease-in_forwards]"
            style={{
              left: `${(i * 37) % 100}%`,
              background: ['var(--color-accent)', 'var(--color-chart)', 'var(--color-ok)', 'var(--color-warn)'][i % 4],
              animationDelay: `${600 + (i % 8) * 70}ms`,
            }}
          />
        ))}
      </div>
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-white/90">Achievement unlocked</p>
      <div className="[perspective:1200px]">
        <div
          key={a.id}
          className="relative [transform-style:preserve-3d] motion-safe:animate-[card-flip_900ms_cubic-bezier(0.2,0.8,0.2,1)_both]"
        >
          <div className="[backface-visibility:hidden]">
            <AchievementCard a={a} number={number} total={total} />
          </div>
          <CardBack />
          <span
            className="pointer-events-none absolute inset-0 hidden rounded-2xl bg-[linear-gradient(105deg,transparent_35%,rgb(255_255_255/0.45)_50%,transparent_65%)] bg-[length:250%_100%] bg-no-repeat motion-safe:block motion-safe:animate-[card-shine_1.1s_ease-out_900ms_both]"
            aria-hidden
          />
        </div>
      </div>
      <div className="flex items-center gap-3">
        {items.length > 1 && (
          <span className="text-xs tabular-nums text-white/80">
            {index + 1} of {items.length}
          </span>
        )}
        {/* eslint-disable-next-line jsx-a11y/no-autofocus -- the dialog's one action */}
        <Button key={a.id} autoFocus variant="primary" onClick={close}>
          {index + 1 < items.length ? 'Next' : 'Nice!'}
        </Button>
      </div>
    </div>
  )
}
