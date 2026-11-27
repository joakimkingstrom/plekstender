import { useEffect, useRef, useState, type ReactNode } from 'react'
import { errorMessage } from '../api/client'
import { imageUrl, useActiveServer } from '../api/library'
import { usePlayerCommand, usePlayerQueue, usePlayers, usePlayerState, type Player, type PlayerCommand, type PlayerState } from '../api/players'
import { Tooltip } from './Tooltip'

const CHOSEN = 'player-widget'

function formatTime(ms: number | null | undefined): string {
  if (ms == null || ms < 0) return '0:00'
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function Icon({ children, filled = false, className = 'size-5' }: { children: ReactNode; filled?: boolean; className?: string }) {
  return (
    <svg viewBox="0 0 20 20" className={className} fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {children}
    </svg>
  )
}

function Control({ label, onClick, active = false, disabled = false, big = false, children }: { label: string; onClick: () => void; active?: boolean; disabled?: boolean; big?: boolean; children: ReactNode }) {
  return (
    <Tooltip content={label}>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        aria-pressed={active || undefined}
        className={`grid place-items-center rounded-full transition disabled:opacity-40 ${
          big ? 'size-11 bg-text text-bg hover:opacity-90' : `size-9 hover:bg-surface-2 ${active ? 'text-accent' : 'text-muted hover:text-text'}`
        }`}
      >
        {children}
      </button>
    </Tooltip>
  )
}

/** How long the player may not answer before the widget says so. */
const QUIET_FOR = 10_000

/**
 * What the player last said, kept through a missed answer or two: `shown` is the latest
 * state it gave (and when), `silent` turns true once it hasn't answered for QUIET_FOR.
 */
function useSteadyState(data: PlayerState | undefined, updatedAt: number) {
  const [seen, setSeen] = useState(0)
  const [good, setGood] = useState<{ state: PlayerState; at: number } | null>(null)
  const [quietSince, setQuietSince] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  // Each new answer once (React's "adjust state while rendering", not an effect).
  if (data && updatedAt !== seen) {
    setSeen(updatedAt)
    if (data.reachable) {
      setGood({ state: data, at: updatedAt })
      setQuietSince(null)
    } else if (quietSince === null) setQuietSince(updatedAt)
  }
  useEffect(() => {
    if (quietSince === null) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [quietSince])
  const silent = quietSince !== null && now - quietSince >= QUIET_FOR
  return { shown: good ?? undefined, silent }
}

/** Where the song is: from the player's last answer, moved on locally while it plays. */
function usePosition(state: PlayerState | undefined, updatedAt: number): number {
  const [now, setNow] = useState(() => Date.now())
  const playing = state?.state === 'playing'
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(id)
  }, [playing])
  const base = state?.position_ms ?? 0
  const moved = playing ? Math.max(0, now - updatedAt) : 0
  return Math.min(base + moved, state?.duration_ms ?? base + moved)
}

/** What a player is playing, the artist first: "Paramore · Pool". */
function nowPlaying(p: Player): string {
  return [p.playing?.artist, p.playing?.title].filter(Boolean).join(' · ')
}

function Chooser({ players, current, onChoose }: { players: Player[]; current: string | null; onChoose: (id: string) => void }) {
  return (
    <ul className="max-h-72 overflow-y-auto py-1" aria-label="Players">
      {players.map((p) => (
        <li key={p.id}>
          <button
            type="button"
            onClick={() => onChoose(p.id)}
            className={`flex w-full items-center gap-3 px-4 py-2 text-left text-sm hover:bg-surface-2 ${p.id === current ? 'text-accent' : ''}`}
          >
            <span className={`size-2 shrink-0 rounded-full ${p.playing ? 'bg-ok' : p.online ? 'bg-border' : 'bg-transparent ring-1 ring-border'}`} aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{p.name}</span>
              <span className="block truncate text-xs text-muted">
                {p.playing ? `${p.playing.state === 'paused' ? 'Paused' : 'Playing'}: ${nowPlaying(p)}` : [p.product, p.platform].filter(Boolean).join(' · ')}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

function Queue({ serverId, playerId, state, onSkip }: { serverId: number; playerId: string; state: PlayerState; onSkip: (itemId: number, key: string) => void }) {
  const queue = usePlayerQueue(serverId, playerId, state.queue_id, true)
  const list = useRef<HTMLOListElement>(null)
  const currentId = state.queue_item_id ?? queue.data?.current_item_id ?? null
  // The song playing at the top (what's coming up below it; what's played above, a scroll
  // away): when the queue opens, and when the next song starts.
  useEffect(() => {
    const ol = list.current
    const row = ol?.querySelector<HTMLElement>('[aria-current="true"]')
    if (ol && row) ol.scrollTop = row.offsetTop - ol.offsetTop
  }, [currentId, queue.data])
  if (!state.queue_id) return <p className="px-4 py-3 text-xs text-muted">This player doesn't say what's queued.</p>
  if (queue.isPending) return <p className="px-4 py-3 text-xs text-muted">Loading the queue…</p>
  if (queue.isError) return <p className="px-4 py-3 text-xs text-danger">{errorMessage(queue.error)}</p>
  const current = state.queue_item_id ?? queue.data.current_item_id
  return (
    <ol ref={list} className="relative max-h-64 overflow-y-auto border-t border-border py-1 text-sm" aria-label="Queue">
      {queue.data.items.map((item) => (
        <li key={item.item_id}>
          <button
            type="button"
            onClick={() => onSkip(item.item_id, item.key)}
            aria-current={item.item_id === current || undefined}
            className={`flex w-full items-baseline gap-2 px-4 py-1.5 text-left hover:bg-surface-2 ${item.item_id === current ? 'text-accent' : ''}`}
          >
            <span className="min-w-0 flex-1 truncate">
              {item.artist && <span className="text-muted">{item.artist} · </span>}
              {item.title}
            </span>
            <span className="shrink-0 text-xs tabular-nums text-muted">{formatTime(item.duration_ms)}</span>
          </button>
        </li>
      ))}
    </ol>
  )
}

function Expanded({ serverId, player, onMinimise, onSwitch }: { serverId: number; player: Player; onMinimise: () => void; onSwitch: () => void }) {
  const state = usePlayerState(serverId, player.id)
  const { shown, silent } = useSteadyState(state.data, state.dataUpdatedAt)
  const command = usePlayerCommand(serverId)
  const [showQueue, setShowQueue] = useState(false)
  const position = usePosition(shown?.state, shown?.at ?? 0)
  const s = shown?.state
  const send = (c: PlayerCommand, value: number | null = null, key: string | null = null) => command.mutate({ playerId: player.id, command: c, value, key })
  const failed = command.data && !command.data.ok ? command.data.detail : command.error ? errorMessage(command.error) : null
  // A button that didn't work says so at once; the player going quiet only once it's been a
  // while (Plexamp now and then doesn't answer for a few seconds, then carries on).
  const problem = failed ?? (silent ? (state.data?.detail ?? (state.isError ? errorMessage(state.error) : null)) : null)
  const can = (what: string) => !s?.controllable.length || s.controllable.includes(what)
  const cover = imageUrl(serverId, s?.thumb, 320)
  const playing = s?.state === 'playing' || s?.state === 'buffering'
  const duration = s?.duration_ms ?? 0
  const repeatLabel = ['Repeat: off', 'Repeat: this song', 'Repeat: all'][s?.repeat ?? 0]
  return (
    <section aria-label={`Playing on ${player.name}`} className="w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-border bg-surface shadow-xl">
      <header className="flex items-center gap-2 px-4 pt-3">
        <button type="button" onClick={onSwitch} className="flex min-w-0 flex-1 items-center gap-1.5 text-left text-xs text-muted hover:text-text" aria-label={`Playing on ${player.name}: choose another player`}>
          <Icon className="size-4">
            <rect x="5" y="2.5" width="10" height="15" rx="2" />
            <circle cx="10" cy="12" r="2.5" />
          </Icon>
          <span className="truncate">{player.name}</span>
          <Icon className="size-3.5">
            <path d="M6 8l4 4 4-4" />
          </Icon>
        </button>
        <Control label="Minimise" onClick={onMinimise}>
          <Icon className="size-4">
            <path d="M5 8l5 5 5-5" />
          </Icon>
        </Control>
      </header>
      <div className="flex gap-3 px-4 pt-2">
        <div className="size-20 shrink-0 overflow-hidden rounded-lg bg-surface-2">{cover && <img src={cover} alt="" className="size-full object-cover" />}</div>
        <div className="min-w-0 flex-1 self-center">
          {s?.title ? (
            <>
              <p className="truncate font-medium" title={s.title}>
                {s.title}
              </p>
              <p className="truncate text-sm text-muted">{s.artist}</p>
              <p className="truncate text-xs text-muted">{s.album}</p>
            </>
          ) : (
            <p className="text-sm text-muted">{s || silent ? 'Nothing playing.' : 'Asking the player…'}</p>
          )}
        </div>
      </div>
      <div className="px-4 pt-3">
        <input
          type="range"
          min={0}
          max={Math.max(duration, 1)}
          step={1000}
          value={Math.min(position, duration)}
          disabled={!duration || !can('seekTo')}
          onChange={(e) => send('seek', Number(e.target.value))}
          aria-label="Position"
          aria-valuetext={`${formatTime(position)} of ${formatTime(duration)}`}
          className="w-full accent-[var(--color-accent)]"
        />
        <div className="flex justify-between text-[11px] tabular-nums text-muted">
          <span>{formatTime(position)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>
      {/* The controls centred, the queue at the right (an empty place at the left keeps play
          in the middle). */}
      <div className="flex items-center justify-between px-3 pb-3">
        <span className="size-9" aria-hidden />
        <Control label={s?.shuffle ? 'Shuffle: on' : 'Shuffle: off'} active={s?.shuffle} disabled={!can('shuffle')} onClick={() => send('shuffle', s?.shuffle ? 0 : 1)}>
          <Icon>
            <path d="M3 6h3l8 8h3M3 14h3l2-2M12 8l2-2h3M15 4l2 2-2 2M15 12l2 2-2 2" />
          </Icon>
        </Control>
        <Control label="Previous" disabled={!can('skipPrevious')} onClick={() => send('previous')}>
          <Icon filled>
            <path d="M5 4v12M16 4.5v11L7 10z" />
          </Icon>
        </Control>
        <Control big label={playing ? 'Pause' : 'Play'} disabled={!can('playPause')} onClick={() => send(playing ? 'pause' : 'play')}>
          {playing ? (
            <Icon>
              <path d="M7 4.5v11M13 4.5v11" strokeWidth="2.4" />
            </Icon>
          ) : (
            <Icon filled>
              <path d="M7 4l9 6-9 6z" />
            </Icon>
          )}
        </Control>
        <Control label="Next" disabled={!can('skipNext')} onClick={() => send('next')}>
          <Icon filled>
            <path d="M15 4v12M4 4.5v11L13 10z" />
          </Icon>
        </Control>
        <Control label={repeatLabel} active={(s?.repeat ?? 0) > 0} disabled={!can('repeat')} onClick={() => send('repeat', ((s?.repeat ?? 0) + 1) % 3)}>
          <Icon>
            <path d="M4 9V7a2 2 0 0 1 2-2h10l-2-2M16 11v2a2 2 0 0 1-2 2H4l2 2" />
            {s?.repeat === 1 && <text x="10" y="12.5" fontSize="6" textAnchor="middle" fill="currentColor" stroke="none">1</text>}
          </Icon>
        </Control>
        <Control label={showQueue ? 'Hide the queue' : 'Show the queue'} active={showQueue} onClick={() => setShowQueue(!showQueue)}>
          <Icon>
            <path d="M3 5h10M3 10h10M3 15h6M15 12v5l3-2.5z" />
          </Icon>
        </Control>
      </div>
      {problem && (
        <p className="px-4 pb-3 text-xs text-danger" role="alert">
          {problem}
        </p>
      )}
      {showQueue && s && <Queue serverId={serverId} playerId={player.id} state={s} onSkip={(itemId, key) => send('skip_to', itemId, key)} />}
    </section>
  )
}

/**
 * Controls for your Plex players, bottom right. It shows (small) while one of the players on
 * your Plex account is playing: choose a player, and it opens with what's playing, the
 * controls (shuffle, previous, play/pause, next, repeat, where in the song) and the queue.
 */
export function PlayerWidget() {
  const { active } = useActiveServer()
  const serverId = active?.id ?? null
  const [chosen, setChosen] = useState<string | null>(() => localStorage.getItem(CHOSEN))
  const [open, setOpen] = useState(false)
  const [choosing, setChoosing] = useState(false)
  const players = usePlayers(serverId, open ? 10_000 : 20_000)
  const mine = (players.data?.players ?? []).filter((p) => p.on_account || p.on_server)
  const playing = mine.filter((p) => p.playing)
  const player = mine.find((p) => p.id === chosen) ?? null
  // Minimised: the chosen player when it's playing, else the first that is.
  const shownPlaying = playing.find((p) => p.id === chosen) ?? playing[0] ?? null
  if (serverId === null || (!open && playing.length === 0)) return null

  const choose = (id: string) => {
    setChosen(id)
    localStorage.setItem(CHOSEN, id)
    setChoosing(false)
    setOpen(true)
  }
  return (
    <div className="fixed right-4 bottom-[calc(env(safe-area-inset-bottom)+5rem)] z-30 flex flex-col items-end gap-2 md:bottom-4">
      {choosing && (
        <div className="w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-border bg-surface shadow-xl">
          <p className="px-4 pt-3 text-xs font-medium text-muted">Control which player?</p>
          <Chooser players={mine} current={chosen} onChoose={choose} />
        </div>
      )}
      {open && player ? (
        <Expanded serverId={serverId} player={player} onMinimise={() => setOpen(false)} onSwitch={() => setChoosing(!choosing)} />
      ) : (
        <button
          type="button"
          onClick={() => setChoosing(!choosing)}
          aria-expanded={choosing}
          className="flex max-w-[min(20rem,calc(100vw-2rem))] items-center gap-3 rounded-full border border-border bg-surface py-2 pr-5 pl-4 text-sm shadow-lg hover:bg-surface-2"
        >
          <span className="flex h-4 items-end gap-0.5 text-accent" aria-hidden>
            <span className="w-1 animate-pulse rounded-sm bg-current" style={{ height: '60%' }} />
            <span className="w-1 animate-pulse rounded-sm bg-current [animation-delay:200ms]" style={{ height: '100%' }} />
            <span className="w-1 animate-pulse rounded-sm bg-current [animation-delay:400ms]" style={{ height: '40%' }} />
          </span>
          <span className="min-w-0 text-left leading-tight">
            <span className="block truncate font-medium">{shownPlaying ? nowPlaying(shownPlaying) : ''}</span>
            <span className="block truncate text-xs text-muted">
              {shownPlaying?.playing?.state === 'paused' ? 'Paused' : 'Playing'} on {shownPlaying?.name}
              {playing.length > 1 && ` · ${playing.length - 1} more`}
            </span>
          </span>
        </button>
      )}
    </div>
  )
}
