import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type Player = Schemas['PlayerOut']
export type PlayerCommand = Schemas['PlayerCommandIn']['command']
export type PlayerRoute = Schemas['PlayerCommandIn']['route']
export type PlayerSent = Schemas['PlayerSentOut']

/** Your Plex players (Plexamp, the Plex apps), with what each is playing. */
export function usePlayers(serverId: number | null) {
  return useQuery({
    queryKey: ['players', serverId],
    queryFn: () => unwrap(api.GET('/api/servers/{server_id}/players', { params: { path: { server_id: serverId! } } })),
    enabled: serverId !== null,
    refetchInterval: 10_000,
  })
}

/** Play, pause, skip on a player: through the server, or straight to it (admins). */
export function usePlayerCommand(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ playerId, command, route }: { playerId: string; command: PlayerCommand; route: PlayerRoute }) =>
      unwrap(
        api.POST('/api/servers/{server_id}/players/{player_id}/command', {
          params: { path: { server_id: serverId, player_id: playerId } },
          body: { command, route },
        }),
      ),
    // What it's playing changes a moment later.
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ['players', serverId] }), 1500),
  })
}

/** Play a song or an album on a player. */
export function usePlayOn(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ playerId, ratingKey, route }: { playerId: string; ratingKey: number; route: PlayerRoute }) =>
      unwrap(
        api.POST('/api/servers/{server_id}/players/{player_id}/play', {
          params: { path: { server_id: serverId, player_id: playerId } },
          body: { rating_key: ratingKey, route },
        }),
      ),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ['players', serverId] }), 2500),
  })
}
