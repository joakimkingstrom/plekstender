import { useEffect, useRef, useState, type CSSProperties } from 'react'
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

/** A steady bit of untidiness per card (the same every time): how it lies in the pile. */
function jitter(id: string) {
  let h = 2166136261
  for (const c of id) h = Math.imul(h ^ c.charCodeAt(0), 16777619)
  const r = (shift: number) => (((h >>> shift) & 0xff) / 255) * 2 - 1
  return { rotate: r(0) * 13, x: r(8) * 34, y: r(16) * 14 }
}

const SHOWN = 6 // cards visible in the pile; the rest sit hidden under the bottom one
const WHEEL_STEP = 60 // pixels of scrolling per card
const WHEEL_PAUSE = 140 // ms between cards, so a trackpad flick doesn't fly through the lot
const WHEEL_SETTLE = 450 // ms after the last card during which the flick's tail is swallowed
const MARGIN = 16 // room kept around the pile when scrolling it into view

/** The collected cards in a messy pile, newest on top. The scroll wheel first brings the
 * whole pile into view, then flips through it, then lets the page scroll on past the last
 * card (or the first, going up). A swipe, a click, the arrow keys and the buttons flip too. */
function CardStack({ cards, numbers, total }: { cards: Achievement[]; numbers: Map<string, number>; total: number }) {
  const [top, setTop] = useState(0)
  const ref = useRef<HTMLDivElement>(null)
  const topRef = useRef(0)
  const n = cards.length
  const go = (to: number) => {
    const next = Math.max(0, Math.min(n - 1, to))
    topRef.current = next
    setTop(next)
  }

  useEffect(() => {
    const el = ref.current
    if (!el || n < 2) return
    let pending = 0
    let last = -Infinity
    // Not React's onWheel: that one can't stop the page scrolling along.
    const onWheel = (e: WheelEvent) => {
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? window.innerHeight : 1
      const vertical = Math.abs(e.deltaY) >= Math.abs(e.deltaX)
      const delta = (vertical ? e.deltaY : e.deltaX) * unit
      if (!delta) return
      const down = delta > 0

      // First the page scrolls until the whole pile is in view (if it fits), and stops there.
      const box = el.getBoundingClientRect()
      const page = document.scrollingElement ?? document.documentElement
      const fits = box.height + 2 * MARGIN <= window.innerHeight
      const short = down
        ? Math.min(box.bottom + MARGIN - window.innerHeight, page.scrollHeight - page.clientHeight - page.scrollTop)
        : Math.min(MARGIN - box.top, page.scrollTop)
      if (vertical && fits && short > 0) {
        e.preventDefault()
        window.scrollBy({ top: down ? Math.min(delta, short) : Math.max(delta, -short) })
        pending = 0
        return
      }

      // Past the last card (or before the first): the page scrolls on, once this flick is over.
      if (down ? topRef.current >= n - 1 : topRef.current <= 0) {
        if (e.timeStamp - last < WHEEL_SETTLE) e.preventDefault()
        pending = 0
        return
      }
      e.preventDefault()
      pending += delta
      if (Math.abs(pending) < WHEEL_STEP || e.timeStamp - last < WHEEL_PAUSE) return
      topRef.current = Math.max(0, Math.min(n - 1, topRef.current + Math.sign(pending)))
      setTop(topRef.current)
      pending = 0
      last = e.timeStamp
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [n])

  const swipe = useRef<number | null>(null)
  return (
    <div className="flex flex-col items-center gap-3">
      <div
        ref={ref}
        role="group"
        aria-roledescription="card stack"
        aria-label="Your achievement cards"
        tabIndex={0}
        className="relative h-[25.5rem] w-full max-w-md touch-pan-y rounded-2xl outline-offset-4"
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight' || e.key === 'ArrowDown') go(top + 1)
          else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') go(top - 1)
          else return
          e.preventDefault()
        }}
        onPointerDown={(e) => (swipe.current = e.clientX)}
        onPointerUp={(e) => {
          if (swipe.current === null) return
          const dx = e.clientX - swipe.current
          swipe.current = null
          if (Math.abs(dx) > 40) go(top + (dx < 0 ? 1 : -1))
          // A click on the last card gathers the pile up again.
          else if (n > 1) go(top + 1 < n ? top + 1 : 0)
        }}
      >
        {cards.map((a, i) => {
          const depth = (i - top + n) % n
          const shown = Math.min(depth, SHOWN - 1)
          const j = jitter(a.id)
          // The top card lies nearly straight so it's easy to read; the ones under it less so.
          const tidy = depth === 0 ? 0.2 : 1
          return (
            <div
              key={a.id}
              aria-hidden={depth !== 0}
              className="absolute left-1/2 top-2 motion-safe:transition-[transform,opacity] motion-safe:duration-300 motion-safe:ease-out"
              style={{
                zIndex: n - depth,
                opacity: depth < SHOWN ? 1 : 0,
                transform: `translateX(-50%) translate(${j.x * tidy}px, ${j.y * tidy + shown * 3}px) rotate(${j.rotate * tidy}deg) scale(${1 - shown * 0.015})`,
              }}
            >
              <AchievementCard a={a} number={numbers.get(a.id) ?? 0} total={total} />
            </div>
          )
        })}
      </div>
      {n > 1 && (
        <div className="flex items-center gap-3 text-sm">
          <Button onClick={() => go(top - 1)} disabled={top === 0} className="px-3 py-1" aria-label="Previous card">
            ‹
          </Button>
          <span className="tabular-nums text-muted" aria-live="polite">
            {top + 1} / {n}
          </span>
          <Button onClick={() => go(top + 1)} disabled={top === n - 1} className="px-3 py-1" aria-label="Next card">
            ›
          </Button>
        </div>
      )}
    </div>
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
        <CardStack cards={collected} numbers={numbers} total={items.length} />
      ) : (
        <p className="text-sm text-muted">None yet: they turn up as you use Plekstender.</p>
      )}
    </div>
  )
}

const CONFETTI = Array.from({ length: 18 }, (_, i) => i)

/** A short celebration when one unlocks (calmer with reduced motion): a spinning record,
 * a little confetti, the name, what it's for and its message. */
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
            <p className="text-sm font-semibold">✓ {a.hint}</p>
            <p className="mt-1 text-sm text-muted">{a.message}</p>
          </div>
        </div>
        <div className="relative mt-3 flex items-center justify-between">
          <span className="text-xs text-muted">{items.length > 1 ? `${index + 1} of ${items.length}` : ''}</span>
          <Button variant="primary" onClick={close} className="px-3 py-1.5 text-sm">
            {index + 1 < items.length ? 'Next' : 'Nice!'}
          </Button>
        </div>
      </div>
    </div>
  )
}
