import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

const KEY = ['admin', 'secondhandsongs'] as const

/** Cover song lookups on SecondHandSongs (admins): on or off, the optional key, progress. */
export function useCoverSongsSettings() {
  return useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET('/api/admin/secondhandsongs')) })
}

export function useSetCoverSongs() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['SecondHandSongsIn']) => unwrap(api.PUT('/api/admin/secondhandsongs', { body })),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  })
}
