import { useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, errorMessage, unwrap } from '../api/client'
import { useActiveServer } from '../api/library'
import { useMe } from '../api/hooks'
import { usePlayerAccess, usePlayerCommand, usePlayers, usePlayOn, useSetPlayerAccess, type Player, type PlayerRoute, type PlayerSent } from '../api/players'
import { Segmented } from '../components/Segmented'
import { Tooltip } from '../components/Tooltip'
import { Badge, Button, Card, Notice, Spinner } from '../components/ui'

type Button5 = 'previous' | 'play' | 'pause' | 'next' | 'stop'
const ICONS: Record<Button5, ReactNode> = {
  previous: <path d="M4 3v10M13 3.5v9L6 8z" />,
  play: <path d="M5 3l8 5-8 5z" />,
  pause: <path d="M5 3.5v9M11 3.5v9" />,
  next: <path d="M12 3v10M3 3.5v9L10 8z" />,
  stop: <rect x="4" y="4" width="8" height="8" rx="1" />,
}
const DOT_ON = 'bg-ok'
const DOT_OFF = 'bg-border'
const NAMES: Record<Button5, string> = { previous: 'Previous', play: 'Play', pause: 'Pause', next: 'Next', stop: 'Stop' }

function useNewestAlbum(serverId: number | null) {
  return useQuery({
    queryKey: ['library', 'recent', serverId, 'newest'],
    queryFn: () =>
      unwrap(api.GET('/api/servers/{server_id}/library/recent', { params: { path: { server_id: serverId! }, query: { kind: 'album', limit: 1 } } })),
    enabled: serverId !== null,
  })
}

function PlayerRow({ serverId, player, directAllowed, album }: { serverId: number; player: Player; directAllowed: boolean; album: { rating_key: number; title: string } | null }) {
  const [route, setRoute] = useState<PlayerRoute>('auto')
  const [result, setResult] = useState<PlayerSent | null>(null)
  const command = usePlayerCommand(serverId)
  const playOn = usePlayOn(serverId)
  const busy = command.isPending || playOn.isPending
  const send = (c: Button5) => command.mutate({ playerId: player.id, command: c, route }, { onSuccess: setResult, onError: () => setResult(null) })
  const error = command.error ?? playOn.error
  const about = [player.product, player.platform, player.version].filter(Boolean).join(' · ')
  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 font-medium">
            <span className={`size-2 shrink-0 rounded-full ${player.online ? DOT_ON : DOT_OFF}`} aria-label={player.online ? 'Online' : 'Offline'} role="img" />
            {player.name}
          </p>
          {about && <p className="text-xs text-muted">{about}</p>}
        </div>
        <div className="flex flex-wrap gap-1">
          {player.on_account && <Badge>On your Plex account</Badge>}
          {player.on_server && <Badge>The server sees it</Badge>}
        </div>
      </div>
      {player.playing && (
        <p className="text-sm">
          <span className="text-muted">{player.playing.state === 'paused' ? 'Paused:' : 'Playing:'}</span> {player.playing.title}
          {player.playing.artist && <span className="text-muted"> · {player.playing.artist}</span>}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1">
          {(Object.keys(ICONS) as Button5[]).map((c) => (
            <Tooltip key={c} content={NAMES[c]}>
              <Button variant="ghost" size="xs" disabled={busy} onClick={() => send(c)} aria-label={`${NAMES[c]} on ${player.name}`}>
                <svg viewBox="0 0 16 16" className="size-4" fill={c === 'pause' || c === 'previous' || c === 'next' ? 'none' : 'currentColor'} stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" aria-hidden>
                  {ICONS[c]}
                </svg>
              </Button>
            </Tooltip>
          ))}
        </div>
        {album && (
          <Button size="xs" disabled={busy} onClick={() => playOn.mutate({ playerId: player.id, ratingKey: album.rating_key, route }, { onSuccess: setResult, onError: () => setResult(null) })}>
            Play {album.title}
          </Button>
        )}
        <Segmented<PlayerRoute>
          label={`How to send to ${player.name}`}
          value={route}
          onChange={setRoute}
          options={[['auto', 'Best way'], ['server', 'Through the server'], ...(directAllowed && player.direct ? ([['direct', 'Straight to it']] as [PlayerRoute, string][]) : [])]}
        />
        {busy && <Spinner small />}
      </div>
      {result && (
        <p className={`text-xs ${result.ok ? 'text-muted' : 'text-danger'}`} role="status">
          {result.route === 'server' ? 'Through the server' : 'Straight to it'}: {result.detail}
        </p>
      )}
      {error && <p className="text-xs text-danger" role="alert">{errorMessage(error)}</p>}
    </li>
  )
}

/** Settings → Players: your Plex players, and trying remote control on each (a first step:
 * which players take commands, and which way, decides what Plekstender builds on it). */
export function PlayersPage() {
  const { active } = useActiveServer()
  const serverId = active?.id ?? null
  const players = usePlayers(serverId)
  const newest = useNewestAlbum(serverId)
  const me = useMe()
  if (!active) return <Notice>Connect a server first.</Notice>
  const album = newest.data?.[0] ?? null
  return (
    <div className="space-y-4">
    {me.data?.is_admin && <PlayerAccessCard />}
    <Card title="Players">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          Plexamp and the Plex apps on your Plex account, and the players {active.name} is in touch with. Try the buttons on each: whether a player
          does what it's asked (and, for admins, which way works) decides how remote control is built.
        </p>
        {players.isPending ? (
          <Spinner />
        ) : players.isError ? (
          <Notice tone="error">{errorMessage(players.error)}</Notice>
        ) : players.data.players.length === 0 ? (
          <Notice>No players found. Open Plexamp or a Plex app (signed in to your account) and look again.</Notice>
        ) : (
          <ul className="divide-y divide-border">
            {players.data.players.map((p) => (
              <PlayerRow key={p.id} serverId={active.id} player={p} directAllowed={players.data.direct_allowed} album={album} />
            ))}
          </ul>
        )}
      </div>
    </Card>
    </div>
  )
}

/** Who may control players straight from Plekstender (admins). */
export function PlayerAccessCard() {
  const access = usePlayerAccess(true)
  const set = useSetPlayerAccess()
  return (
    <Card title="Who can control players">
      <div className="space-y-3 text-sm">
        <p className="text-muted">
          When the Plex server isn't in touch with a player (Plexamp on another network, say), Plekstender sends it commands itself, to the
          player's address on your network. Everyone can control players the server is in touch with.
        </p>
        {access.data && (
          <Segmented<'admins' | 'everyone'>
            label="Who can control players straight from Plekstender"
            value={access.data.access}
            onChange={(v) => set.mutate(v)}
            options={[['admins', 'Admins only'], ['everyone', 'Everyone']]}
          />
        )}
        {set.isError && <Notice tone="error">{errorMessage(set.error)}</Notice>}
      </div>
    </Card>
  )
}
