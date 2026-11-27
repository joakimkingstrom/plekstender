import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type Player = Schemas['PlayerOut']
export type PlayerState = Schemas['PlayerStateOut']
export type PlayerCommand = Schemas['PlayerCommandIn']['command']
export type PlayerRoute = NonNullable<Schemas['PlayerCommandIn']['route']>
export type PlayerSent = Schemas['PlayerSentOut']
export type QueueItem = Schemas['QueueItemOut']

/** Your Plex players (Plexamp, the Plex apps), with what each is playing. */
export function usePlayers(serverId: number | null, every = 10_000) {
  return useQuery({
    queryKey: ['players', serverId],
    queryFn: () => unwrap(api.GET('/api/servers/{server_id}/players', { params: { path: { server_id: serverId! } } })),
    enabled: serverId !== null,
    refetchInterval: every,
    retry: false,
  })
}

/** What one player is doing, as it says itself: asked every couple of seconds. */
export function usePlayerState(serverId: number, playerId: string | null) {
  return useQuery({
    queryKey: ['players', serverId, playerId, 'state'],
    queryFn: () =>
      unwrap(api.GET('/api/servers/{server_id}/players/{player_id}/state', { params: { path: { server_id: serverId, player_id: playerId! } } })),
    enabled: playerId !== null,
    refetchInterval: 2000,
    retry: false,
  })
}

/** A player's queue (the play queue its state names). */
export function usePlayerQueue(serverId: number, playerId: string | null, queueId: number | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['players', serverId, playerId, 'queue', queueId],
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/players/{player_id}/queue', {
          params: { path: { server_id: serverId, player_id: playerId! }, query: { queue_id: queueId! } },
        }),
      ),
    enabled: enabled && playerId !== null && Boolean(queueId),
    refetchInterval: 15_000,
    retry: false,
  })
}

type CommandVars = { playerId: string; command: PlayerCommand; route?: PlayerRoute; value?: number | null; key?: string | null }

/** Play, pause, skip, seek, shuffle, repeat or go to a song in the queue. "auto" goes through
 * the server when it's in touch with the player, else straight to it. */
export function usePlayerCommand(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ playerId, command, route = 'auto', value = null, key = null }: CommandVars) =>
      unwrap(
        api.POST('/api/servers/{server_id}/players/{player_id}/command', {
          params: { path: { server_id: serverId, player_id: playerId } },
          body: { command, route, value, key },
        }),
      ),
    // The player takes a moment: look again shortly.
    onSuccess: (_, { playerId }) =>
      setTimeout(() => void qc.invalidateQueries({ queryKey: ['players', serverId, playerId] }), 400),
  })
}

/** Play a song or an album on a player. */
export function usePlayOn(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ playerId, ratingKey, route = 'auto' }: { playerId: string; ratingKey: number; route?: PlayerRoute }) =>
      unwrap(
        api.POST('/api/servers/{server_id}/players/{player_id}/play', {
          params: { path: { server_id: serverId, player_id: playerId } },
          body: { rating_key: ratingKey, route },
        }),
      ),
    onSuccess: () => setTimeout(() => void qc.invalidateQueries({ queryKey: ['players', serverId] }), 1500),
  })
}

/** Who may control players (admins). */
export function usePlayerAccess(enabled: boolean) {
  return useQuery({
    queryKey: ['players', 'access'],
    queryFn: () => unwrap(api.GET('/api/admin/players')),
    enabled,
  })
}

export function useSetPlayerAccess() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (access: 'admins' | 'everyone') => unwrap(api.PUT('/api/admin/players', { body: { access } })),
    onSuccess: (data) => {
      qc.setQueryData(['players', 'access'], data)
      void qc.invalidateQueries({ queryKey: ['players'] })
    },
  })
}
