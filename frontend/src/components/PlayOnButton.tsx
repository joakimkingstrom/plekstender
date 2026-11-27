import { useEffect, useRef, useState } from 'react'
import { errorMessage } from '../api/client'
import { usePlayers, usePlayOn } from '../api/players'
import { Tooltip } from './Tooltip'

/** Play a song or an album on one of your Plex players (Plexamp, the Plex apps): a speaker
 * button that opens your players; choose one and it plays there. */
export function PlayOnButton({ serverId, ratingKey, title, size = 'size-7' }: { serverId: number; ratingKey: number; title: string; size?: string }) {
  const [open, setOpen] = useState(false)
  const [up, setUp] = useState(false)
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const players = usePlayers(open ? serverId : null, false)
  const playOn = usePlayOn(serverId)
  const list = (players.data?.players ?? []).filter((p) => p.on_account || p.on_server)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  useEffect(() => {
    if (!said) return
    const id = setTimeout(() => setSaid(null), said.ok ? 4000 : 10_000)
    return () => clearTimeout(id)
  }, [said])

  const toggle = () => {
    if (!open && box.current) setUp(window.innerHeight - box.current.getBoundingClientRect().bottom < 260)
    setOpen(!open)
  }
  const play = (playerId: string, name: string) => {
    setOpen(false)
    playOn.mutate(
      { playerId, ratingKey },
      {
        onSuccess: (sent) => setSaid(sent.ok ? { ok: true, text: `Playing on ${name}` } : { ok: false, text: sent.detail }),
        onError: (e) => setSaid({ ok: false, text: errorMessage(e) }),
      },
    )
  }
  return (
    <div ref={box} className="relative shrink-0" onPointerDown={(e) => e.stopPropagation()}>
      <Tooltip content="Play on…">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            toggle()
          }}
          aria-label={`Play ${title} on…`}
          aria-expanded={open}
          className={`flex ${size} items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-text ${playOn.isPending ? 'animate-pulse' : ''}`}
        >
          <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <rect x="3.5" y="1.5" width="9" height="13" rx="1.5" />
            <circle cx="8" cy="9.5" r="2.5" />
            <circle cx="8" cy="4.5" r="0.6" fill="currentColor" />
          </svg>
        </button>
      </Tooltip>
      {open && (
        <div
          role="menu"
          aria-label={`Play ${title} on`}
          className={`absolute right-0 z-30 w-64 overflow-hidden rounded-xl border border-border bg-surface py-1 text-sm shadow-xl ${up ? 'bottom-full mb-1' : 'top-full mt-1'}`}
        >
          <p className="px-3 pt-1.5 pb-1 text-xs text-muted">Play on</p>
          {players.isPending ? (
            <p className="px-3 py-2 text-xs text-muted">Looking for your players…</p>
          ) : players.isError ? (
            <p className="px-3 py-2 text-xs text-danger">{errorMessage(players.error)}</p>
          ) : list.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted">No players found. Open Plexamp or a Plex app and try again.</p>
          ) : (
            list.map((p) => (
              <button
                key={p.id}
                type="button"
                role="menuitem"
                onClick={() => play(p.id, p.name)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-surface-2"
              >
                <span className={`size-2 shrink-0 rounded-full ${p.playing ? 'bg-ok' : p.online ? 'bg-border' : 'ring-1 ring-border'}`} aria-hidden />
                <span className={`min-w-0 flex-1 truncate ${p.online ? '' : 'text-muted'}`}>{p.name}</span>
                <span className="shrink-0 text-xs text-muted">{p.product}</span>
              </button>
            ))
          )}
        </div>
      )}
      {said && (
        <p
          role={said.ok ? 'status' : 'alert'}
          className={`absolute right-0 z-30 mt-1 w-64 rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg ${said.ok ? 'text-muted' : 'text-danger'}`}
        >
          {said.text}
        </p>
      )}
    </div>
  )
}
