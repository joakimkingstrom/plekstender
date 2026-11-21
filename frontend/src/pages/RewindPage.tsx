import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router'
import { api, errorMessage, unwrap, type Schemas } from '../api/client'
import type { GameMode } from '../api/game'
import { imageUrl, useActiveServer } from '../api/library'
import { localTimeZone } from '../api/stats'
import { playSample, stopAudio, unlockAudio, useAudio } from '../audio'
import { DatePicker } from '../components/DatePicker'
import { Button, Card, Notice, Spinner } from '../components/ui'
import { formatCount, formatListeningTime } from '../format'

type Rewind = Schemas['RewindOut']
type Item = Schemas['RewindItem']
type Range = { start: string; end: string; label: string }

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

function presets(today = new Date()): Range[] {
  const y = today.getFullYear()
  const m = today.getMonth()
  const monthName = (d: Date) => d.toLocaleString(undefined, { month: 'long', year: 'numeric' })
  const lastMonth = new Date(y, m - 1, 1)
  return [
    { start: `${y}-01-01`, end: iso(today), label: `${y} so far` },
    { start: `${y - 1}-01-01`, end: `${y - 1}-12-31`, label: String(y - 1) },
    { start: iso(new Date(y, m, 1)), end: iso(today), label: monthName(today) },
    { start: iso(lastMonth), end: iso(new Date(y, m, 0)), label: monthName(lastMonth) },
    { start: iso(new Date(y, m, today.getDate() - 29)), end: iso(today), label: 'The last 30 days' },
  ]
}

/** History → Rewind: pick a period, then play its story. */
export function RewindPage() {
  const options = useMemo(() => presets(), [])
  const today = options[0].end
  const [range, setRange] = useState<Range>(options[0])
  const [custom, setCustom] = useState({ start: options[4].start, end: options[4].end })
  const fromId = useId()
  const toId = useId()
  const [playing, setPlaying] = useState<Range | null>(null)
  return (
    <div className="space-y-4">
      <Card title="Rewind">
        <p className="mb-4 text-sm text-muted">
          A look back at any stretch of your listening: your top artists, albums and songs, new obsessions, when you listen,
          what you had on repeat and more, one card at a time.
        </p>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Period">
          {options.map((o) => (
            <button
              key={o.label}
              type="button"
              aria-pressed={range.label === o.label}
              onClick={() => setRange(o)}
              className={`rounded-full border px-3 py-1.5 text-sm font-medium ${
                range.label === o.label ? 'border-accent bg-accent text-on-accent' : 'border-border text-muted hover:text-text'
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
        <form
          className="mt-3 flex flex-wrap items-end gap-2 text-sm"
          onSubmit={(e) => {
            e.preventDefault()
            if (custom.start && custom.end && custom.start <= custom.end) setPlaying({ ...custom, label: `${custom.start} – ${custom.end}` })
          }}
        >
          <div>
            <span id={fromId} className="mb-1 block text-xs text-muted">
              From
            </span>
            <DatePicker value={custom.start} max={custom.end || today} onChange={(start) => setCustom({ ...custom, start })} labelledBy={fromId} size="sm" />
          </div>
          <div>
            <span id={toId} className="mb-1 block text-xs text-muted">
              To
            </span>
            <DatePicker value={custom.end} min={custom.start} max={today} onChange={(end) => setCustom({ ...custom, end })} labelledBy={toId} size="sm" />
          </div>
          <Button type="submit" variant="ghost">
            Play these dates
          </Button>
        </form>
        <div className="mt-4">
          <Button variant="primary" onClick={() => setPlaying(range)}>
            Play {range.label}
          </Button>
        </div>
      </Card>
      {playing && <RewindStory range={playing} onClose={() => setPlaying(null)} />}
    </div>
  )
}

// Vivid fallbacks for periods whose covers have no colours yet.
const FALLBACK = ['#e4572e', '#f2ad45', '#7b2cbf', '#2a9d8f', '#3a86ff', '#d6336c']

function useRewind(range: Range, serverId: number | null) {
  return useQuery({
    queryKey: ['rewind', range.start, range.end, serverId],
    staleTime: 10 * 60_000,
    queryFn: () =>
      unwrap(
        api.GET('/api/rewind', {
          params: { query: { start: range.start, end: range.end, tz: localTimeZone(), server_id: serverId ?? undefined } },
        }),
      ),
  })
}

const WEEKDAYS = Array.from({ length: 7 }, (_, i) => new Date(2026, 10, 16 + i).toLocaleString(undefined, { weekday: 'long' }))
const WEEKDAYS_SHORT = Array.from({ length: 7 }, (_, i) => new Date(2026, 10, 16 + i).toLocaleString(undefined, { weekday: 'short' }))
const PARTS: Record<string, string> = { night: 'at night', morning: 'in the morning', afternoon: 'in the afternoon', evening: 'in the evening' }
const clockTime = (h: number) => new Date(2026, 0, 1, h).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
const hourName = (h: number) => new Date(2026, 0, 1, h).toLocaleTimeString(undefined, { hour: 'numeric' })

/** A song for a slide: an artist's or album's popular one, or the song itself. */
type Music = { mode: GameMode; key: number }
type Slide = { key: string; colour: string; body: ReactNode; music?: Music }

const tune = (mode: GameMode, key: number | null | undefined): Music | undefined => (key ? { mode, key } : undefined)

const SOUND_KEY = 'plekstender.rewind.sound'
function readSound(): boolean {
  try {
    return localStorage.getItem(SOUND_KEY) === 'on'
  } catch {
    return false
  }
}
function saveSound(on: boolean) {
  try {
    localStorage.setItem(SOUND_KEY, on ? 'on' : 'off')
  } catch {
    // storage unavailable: the choice lasts for this Rewind
  }
}

/** The full-screen story: one card at a time, coloured by the period's album covers. */
function RewindStory({ range, onClose }: { range: Range; onClose: () => void }) {
  const { active } = useActiveServer()
  const serverId = active && active.music_section_keys.length > 0 ? active.id : null
  const rewind = useRewind(range, serverId)
  const [index, setIndex] = useState(0)
  const closeButton = useRef<HTMLButtonElement>(null)
  const slides = useMemo(() => (rewind.data ? buildSlides(rewind.data, range, serverId) : []), [rewind.data, range, serverId])
  const last = slides.length - 1
  const go = useCallback((step: number) => setIndex((i) => Math.max(0, Math.min(last, i + step))), [last])

  // Music (optional, off until asked for): each slide's song, or the last one's carrying on.
  const [sound, setSound] = useState(readSound)
  const audio = useAudio()
  const hasMusic = serverId !== null && slides.some((sl) => sl.music)
  const music = useMemo(() => {
    for (let i = index; i >= 0; i--) if (slides[i]?.music) return slides[i].music
    return undefined
  }, [slides, index])
  const musicMode = music?.mode
  const musicKey = music?.key
  useEffect(() => {
    if (!sound || serverId === null || !musicMode || !musicKey) return
    void playSample(serverId, musicMode, musicKey)
  }, [sound, serverId, musicMode, musicKey])
  useEffect(() => () => stopAudio(), [])
  const toggleSound = () => {
    if (sound) stopAudio()
    else unlockAudio() // a tap: lets iPhones play the songs
    saveSound(!sound)
    setSound(!sound)
  }

  useEffect(() => {
    closeButton.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight' || e.key === ' ') go(1)
      else if (e.key === 'ArrowLeft') go(-1)
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
    }
  }, [go, onClose])

  const slide = slides[index]
  const next = slides[Math.min(last, index + 1)]
  const background = slide
    ? `linear-gradient(150deg, color-mix(in oklab, ${slide.colour} 78%, black), color-mix(in oklab, ${next?.colour ?? slide.colour} 45%, black))`
    : '#17181c'

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Rewind: ${range.label}`}
      data-colour={slide?.colour}
      className="fixed inset-0 z-50 flex flex-col text-white motion-safe:transition-[background] motion-safe:duration-700"
      style={{ background }}
    >
      <div className="flex items-center gap-2 px-4 pt-[max(1rem,env(safe-area-inset-top))]">
        <div className="flex flex-1 gap-1" aria-hidden>
          {slides.map((s, i) => (
            <span key={s.key} className={`h-1 flex-1 rounded-full ${i <= index ? 'bg-white' : 'bg-white/30'}`} />
          ))}
        </div>
        {hasMusic && (
          <button
            type="button"
            onClick={toggleSound}
            aria-pressed={sound}
            aria-label={sound ? 'Turn the music off' : 'Play music'}
            title={sound ? 'Turn the music off' : 'Play the songs and artists as you go'}
            className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-semibold hover:bg-white/15 ${sound ? 'bg-white/20' : ''}`}
          >
            <SpeakerIcon on={sound} className={`size-5 ${sound && audio.status === 'loading' ? 'animate-pulse' : ''}`} />
            <span className="max-sm:sr-only">{sound ? 'Music on' : 'Music'}</span>
          </button>
        )}
        <button ref={closeButton} type="button" onClick={onClose} aria-label="Close Rewind" className="rounded-full p-2 text-2xl leading-none hover:bg-white/15">
          ×
        </button>
      </div>
      {/* Scrolls when a slide is taller than the screen (centred with auto margins, so the
          top stays reachable). A tap on the left third goes back, anywhere else on. */}
      <div
        className="flex min-h-0 flex-1 cursor-pointer flex-col overflow-y-auto overscroll-contain px-5 py-4 sm:px-6 sm:py-6"
        onClick={(e) => {
          if (slides.length < 2 || (e.target as HTMLElement).closest('a, button')) return
          const box = e.currentTarget.getBoundingClientRect()
          go(e.clientX - box.left < box.width / 3 ? -1 : 1)
        }}
      >
        {rewind.isPending ? (
          <div className="m-auto">
            <Spinner />
          </div>
        ) : rewind.isError ? (
          <div className="m-auto max-w-md">
            <Notice tone="error">{errorMessage(rewind.error)}</Notice>
          </div>
        ) : slide ? (
          <div key={slide.key} className="mx-auto my-auto w-full max-w-xl motion-safe:animate-[rewind-in_500ms_ease-out]" aria-live="polite">
            {slide.body}
          </div>
        ) : null}
      </div>
      <div className="flex items-center justify-between gap-2 px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
        <button type="button" onClick={() => go(-1)} disabled={index === 0} className="rounded-full px-4 py-2 text-sm font-semibold hover:bg-white/15 disabled:opacity-40">
          ← Back
        </button>
        <span className="text-xs text-white/70 tabular-nums">
          {slides.length ? `${index + 1} / ${slides.length}` : ''}
        </span>
        {index < last ? (
          <button type="button" onClick={() => go(1)} className="rounded-full bg-white px-4 py-2 text-sm font-semibold text-black hover:bg-white/90">
            Next →
          </button>
        ) : (
          <button type="button" onClick={onClose} className="rounded-full bg-white px-4 py-2 text-sm font-semibold text-black hover:bg-white/90">
            Done
          </button>
        )}
      </div>
    </div>,
    document.body,
  )
}

function SpeakerIcon({ on, className }: { on: boolean; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor" />
      {on ? <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" /> : <path d="m16 9 5 6m0-6-5 6" />}
    </svg>
  )
}

function Heading({ kicker, children }: { kicker: string; children: ReactNode }) {
  return (
    <>
      <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-white/75 sm:text-sm">{kicker}</p>
      <h2 className="mb-4 font-display text-3xl font-bold leading-tight sm:mb-6 sm:text-5xl">{children}</h2>
    </>
  )
}

function Cover({ serverId, item, round = false, big = false }: { serverId: number | null; item: Item; round?: boolean; big?: boolean }) {
  const src = serverId !== null ? imageUrl(serverId, item.thumb, big ? 600 : 128) : null
  const size = big ? 'size-32 sm:size-52' : 'size-10 sm:size-12'
  return src ? (
    <img src={src} alt="" className={`${size} shrink-0 object-cover shadow-lg ${round ? 'rounded-full' : 'rounded-md'}`} />
  ) : (
    <span className={`${size} shrink-0 bg-white/15 ${round ? 'rounded-full' : 'rounded-md'}`} />
  )
}

function Ranked({ items, serverId, round = false, unit = 'listens' }: { items: Item[]; serverId: number | null; round?: boolean; unit?: string }) {
  return (
    <ol className="space-y-2 sm:space-y-3">
      {items.map((item, i) => (
        <li key={`${item.name}-${item.artist}`} className="flex items-center gap-3">
          <span className="w-5 text-right font-display text-xl font-bold text-white/70 sm:w-6 sm:text-2xl">{i + 1}</span>
          <Cover serverId={serverId} item={item} round={round} />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-base font-semibold sm:text-lg">{item.name}</span>
            {item.artist && <span className="block truncate text-sm text-white/75">{item.artist}</span>}
          </span>
          <span className="shrink-0 text-xs text-white/80 tabular-nums sm:text-sm">
            {formatCount(item.listens)} {unit}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** A small column chart; `ticks` are the labels shown under the bars ('' for none). */
function Bars({ values, labels, ticks, highlight }: { values: number[]; labels: string[]; ticks: string[]; highlight: number }) {
  const max = Math.max(1, ...values)
  return (
    <div>
      <div className="flex h-32 items-end gap-1" role="img" aria-label={labels.map((l, i) => `${l}: ${values[i]}`).join(', ')}>
        {values.map((v, i) => (
          <div
            key={i}
            className={`flex-1 rounded-t ${i === highlight ? 'bg-white' : 'bg-white/40'}`}
            style={{ height: `${Math.max(2, (v / max) * 100)}%` }}
          />
        ))}
      </div>
      <div className="mt-1 flex gap-1 text-[10px] text-white/60" aria-hidden>
        {ticks.map((t, i) => (
          <span key={i} className="flex-1 overflow-visible whitespace-nowrap">
            {t}
          </span>
        ))}
      </div>
    </div>
  )
}

const NUMBER_WORDS = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']

/** "3 times in a row"; when it happened more than once, "… on two different occasions"
 * (with "2 to 5 times" when the runs weren't all as long). */
function onRepeat(s: { runs: number; longest: number; shortest: number }): string {
  const times = s.shortest && s.shortest !== s.longest ? `${s.shortest} to ${s.longest}` : `${s.longest}`
  if (s.runs < 2) return `${times} times in a row`
  return `${times} times in a row on ${NUMBER_WORDS[s.runs] ?? formatCount(s.runs)} different occasions`
}

function Big({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <p className="font-display text-5xl font-bold tabular-nums sm:text-6xl">{value}</p>
      <p className="text-white/80">{label}</p>
    </div>
  )
}

function buildSlides(r: Rewind, range: Range, serverId: number | null): Slide[] {
  const palette = [...new Set([...r.top_albums, ...r.top_tracks, ...r.new_albums, ...r.new_releases].map((i) => i.colour).filter((c): c is string => Boolean(c)))]
  const colours = palette.length >= 3 ? palette : [...palette, ...FALLBACK]
  const slides: Omit<Slide, 'colour'>[] = []
  if (r.listens === 0) {
    slides.push({
      key: 'empty',
      body: (
        <>
          <Heading kicker={range.label}>Nothing to look back on yet</Heading>
          <p className="text-lg text-white/85">There are no listens in these dates. Pick another period, or import your history.</p>
        </>
      ),
    })
    return slides.map((s, i) => ({ ...s, colour: colours[i % colours.length] }))
  }
  slides.push({
    key: 'intro',
    music: tune('track', r.top_tracks[0]?.track_key),
    body: (
      <>
        <Heading kicker="Rewind">{range.label}</Heading>
        <div className="grid gap-6">
          <Big value={formatCount(r.listens)} label="listens" />
          <Big value={formatListeningTime(r.listening_ms)} label="of music" />
          <Big value={`${formatCount(r.days)} of ${formatCount(r.period_days)}`} label="days with music" />
        </div>
      </>
    ),
  })
  const [topArtist] = r.top_artists
  if (topArtist)
    slides.push({
      key: 'artists',
      music: tune('artist', topArtist.rating_key),
      body: (
        <>
          <Heading kicker="Your top artist">{topArtist.name}</Heading>
          <div className="mb-6 flex justify-center">
            <Cover serverId={serverId} item={topArtist} round big />
          </div>
          <Ranked items={r.top_artists} serverId={serverId} round />
        </>
      ),
    })
  if (r.top_albums.length)
    slides.push({
      key: 'albums',
      music: tune('album', r.top_albums[0].rating_key),
      body: (
        <>
          <Heading kicker="Your top albums">{r.top_albums[0].name}</Heading>
          <Ranked items={r.top_albums} serverId={serverId} />
        </>
      ),
    })
  if (r.top_tracks.length)
    slides.push({
      key: 'songs',
      music: tune('track', r.top_tracks[0].track_key),
      body: (
        <>
          <Heading kicker="Your top songs">{r.top_tracks[0].name}</Heading>
          <Ranked items={r.top_tracks} serverId={serverId} />
        </>
      ),
    })
  if (r.new_artists.length || r.new_albums.length)
    slides.push({
      key: 'new',
      music: tune('artist', r.new_artists[0]?.rating_key) ?? tune('album', r.new_albums[0]?.rating_key),
      body: (
        <>
          <Heading kicker="New obsessions">First heard, then on and on</Heading>
          {r.new_artists.length > 0 && (
            <>
              <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-white/60">Artists</p>
              <Ranked items={r.new_artists} serverId={serverId} round />
            </>
          )}
          {r.new_albums.length > 0 && (
            <div className={r.new_artists.length ? 'mt-5' : ''}>
              <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-white/60">Albums</p>
              <Ranked items={r.new_albums} serverId={serverId} />
            </div>
          )}
        </>
      ),
    })
  if (r.new_releases.length)
    slides.push({
      key: 'releases',
      music: tune('album', r.new_releases[0].rating_key),
      body: (
        <>
          <Heading kicker="Fresh out">The new releases you played most</Heading>
          <Ranked items={r.new_releases} serverId={serverId} />
        </>
      ),
    })
  const busiestDay = r.weekdays.indexOf(Math.max(...r.weekdays))
  const busiestHour = r.hours.indexOf(Math.max(...r.hours))
  slides.push({
    key: 'when',
    body: (
      <>
        <Heading kicker="When you listen">
          {WEEKDAYS[busiestDay]}s, around {clockTime(busiestHour)}
        </Heading>
        <Bars values={r.weekdays} labels={WEEKDAYS} ticks={WEEKDAYS_SHORT} highlight={busiestDay} />
        <p className="mt-2 mb-6 text-sm text-white/75">Listens by day of the week, Monday first</p>
        <Bars
          values={r.hours}
          labels={r.hours.map((_, h) => hourName(h))}
          ticks={r.hours.map((_, h) => (h % 6 === 0 ? hourName(h) : ''))}
          highlight={busiestHour}
        />
        <p className="mt-2 text-sm text-white/75">…and by hour of the day</p>
      </>
    ),
  })
  if (r.genres.length)
    slides.push({
      key: 'genres',
      body: (
        <>
          <Heading kicker="Your sound">{r.genres[0].genre}</Heading>
          <ul className="mb-6 flex flex-wrap gap-2">
            {r.genres.map((g) => (
              <li key={g.genre} className="rounded-full bg-white/15 px-3 py-1 text-sm">
                {g.genre}
              </li>
            ))}
          </ul>
          <ul className="space-y-1 text-lg">
            {r.genre_parts.map((g) => (
              <li key={g.when}>
                <span className="font-semibold">{g.genre}</span> <span className="text-white/75">{PARTS[g.when] ?? g.when}</span>
              </li>
            ))}
          </ul>
          {r.genre_days.length > 0 && (
            <ul className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-white/85">
              {r.genre_days.map((g) => (
                <li key={g.when}>
                  {WEEKDAYS[Number(g.when)]}: <span className="font-semibold text-white">{g.genre}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      ),
    })
  if (r.longest_repeat)
    slides.push({
      key: 'repeat',
      music: tune('track', r.longest_repeat.track_key),
      body: (
        <>
          <Heading kicker="On repeat">
            {r.longest_repeat.name}, {r.longest_repeat.longest} times in a row
          </Heading>
          <p className="mb-6 text-white/80">by {r.longest_repeat.artist}</p>
          <ol className="space-y-2">
            {r.repeats.map((s) => (
              <li key={`${s.name}-${s.artist}`} className="flex justify-between gap-3">
                <span className="min-w-0 truncate">
                  <span className="font-semibold">{s.name}</span> <span className="text-white/75">· {s.artist}</span>
                </span>
                <span className="shrink-0 text-sm text-white/80">{onRepeat(s)}</span>
              </li>
            ))}
          </ol>
        </>
      ),
    })
  if (r.game)
    slides.push({
      key: 'game',
      body: (
        <>
          <Heading kicker="Guess the intro">{formatCount(r.game.points)} points</Heading>
          <div className="grid grid-cols-2 gap-6">
            <Big value={formatCount(r.game.rounds)} label="rounds played" />
            <Big value={`${Math.round((100 * r.game.correct) / Math.max(1, r.game.rounds))}%`} label="guessed right" />
            <Big value={formatCount(r.game.best_streak)} label="best streak" />
            <Big value={formatCount(r.game.days)} label={r.game.days === 1 ? 'day playing' : 'days playing'} />
          </div>
        </>
      ),
    })
  if (r.app_seconds)
    slides.push({
      key: 'app',
      body: (
        <>
          <Heading kicker="Time in Plekstender">{formatListeningTime(r.app_seconds * 1000)}</Heading>
          <p className="text-lg text-white/85">rating, playing, browsing and tinkering with your library.</p>
        </>
      ),
    })
  slides.push({
    key: 'outro',
    body: (
      <>
        <Heading kicker="That was">{range.label}</Heading>
        <p className="mb-6 text-lg text-white/85">
          {formatCount(r.artists)} artists, {formatCount(r.tracks)} different songs.
          {topArtist ? ` And more ${topArtist.name} than anything else.` : ''}
        </p>
        <Link to="/history" className="text-white underline">
          More in History
        </Link>
      </>
    ),
  })
  return slides.map((s, i) => ({ ...s, colour: colours[i % colours.length] }))
}
