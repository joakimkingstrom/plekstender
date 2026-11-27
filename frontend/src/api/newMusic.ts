import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type NewMusic = Schemas['NewMusicOut']
export type NewMusicChoice = Schemas['NewMusicIn']

const key = (serverId: number) => ['servers', serverId, 'new-music'] as const

/** What's done for new artists and albums on a server (its owner's choices). */
export function useNewMusic(serverId: number) {
  return useQuery({
    queryKey: key(serverId),
    queryFn: () => unwrap(api.GET('/api/servers/{server_id}/new-music', { params: { path: { server_id: serverId } } })),
  })
}

export function useSetNewMusic(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: NewMusicChoice) => unwrap(api.PUT('/api/servers/{server_id}/new-music', { params: { path: { server_id: serverId } }, body })),
    onSuccess: (data) => qc.setQueryData(key(serverId), data),
  })
}
