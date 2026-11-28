import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

const KEY = ['admin', 'cover-songs'] as const

/** Cover song lookups (admins): on or off, the sources, SecondHandSongs' optional key, progress. */
export function useCoverSongsSettings() {
  return useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/admin/cover-songs')) })
}

export function useSetCoverSongs() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['CoverSongsIn']) => unwrap(api.PUT('/api/admin/cover-songs', { body })),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  })
}

/** Which files would get the original artist of the cover they are (nothing is written). */
export function useCoverTagsPreview(serverId: number) {
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/servers/{server_id}/files/cover-tags/preview', { params: { path: { server_id: serverId } } })),
  })
}
