import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'
import { liveKeys } from './live'

export type Gig = Schemas['GigOut']
export type GigIn = Schemas['GigIn']

export const gigKeys = {
  all: ['gigs'] as const,
  list: (year: number | null, artist: string) => [...gigKeys.all, 'list', year, artist] as const,
}

export function useGigs(year: number | null, artist: string) {
  return useQuery({
    queryKey: gigKeys.list(year, artist),
    queryFn: () =>
      unwrap(api.GET('/api/gigs', { params: { query: { year: year ?? undefined, artist: artist || undefined } } })),
    placeholderData: keepPreviousData,
  })
}

export function useGigArtists() {
  return useQuery({ queryKey: [...gigKeys.all, 'artists'], queryFn: () => unwrap(api.GET('/api/gigs/artists')) })
}

export function useGigSuggestions() {
  return useQuery({ queryKey: [...gigKeys.all, 'suggestions'], queryFn: () => unwrap(api.GET('/api/gigs/suggestions')) })
}

export function useSaveGig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, gig }: { id?: number; gig: GigIn }) =>
      id
        ? unwrap(api.PUT('/api/gigs/{gig_id}', { params: { path: { gig_id: id } }, body: gig }))
        : unwrap(api.POST('/api/gigs', { body: gig })),
    onSuccess: () => qc.invalidateQueries({ queryKey: gigKeys.all }),
  })
}

export function useDeleteGig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => unwrap(api.DELETE('/api/gigs/{gig_id}', { params: { path: { gig_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: gigKeys.all }),
  })
}

export function useRecordLiveGig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids: number[]) => unwrap(api.POST('/api/gigs/from-live', { body: { ids } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: gigKeys.all })
      qc.invalidateQueries({ queryKey: liveKeys.all })
    },
  })
}
