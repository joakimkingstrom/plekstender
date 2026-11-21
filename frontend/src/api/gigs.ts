import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, postRaw, unwrap, type Schemas } from './client'
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

export type GigSetlist = Schemas['GigSetlist']

/** A link to a setlist on setlist.fm (the form looks those up). */
export const SETLIST_LINK = /setlist\.fm\/setlist\/(?:[^?#]*?-)?[0-9a-f]{6,10}\.html/i

/** A gig as setlist.fm has it, from a link to one of its setlists. */
export function useSetlistLookup() {
  return useMutation({
    mutationFn: (url: string) => unwrap(api.POST('/api/gigs/setlistfm/lookup', { body: { url } })),
  })
}

/** The gigs you've been to where a library artist played (for their page). */
export function useArtistPastGigs(serverId: number, ratingKey: number) {
  return useQuery({
    queryKey: [...gigKeys.all, 'artist', serverId, ratingKey],
    queryFn: () =>
      unwrap(api.GET('/api/gigs/artist/{server_id}/{rating_key}', { params: { path: { server_id: serverId, rating_key: ratingKey } } })),
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

export type GigImport = Schemas['GigImportOut']

/** Import gigs: from setlist.fm (a username), or a CSV / iCalendar file. */
export function useImportGigs() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (what: { setlistfm: string } | { file: File; kind: 'csv' | 'ical' }) =>
      'setlistfm' in what
        ? unwrap(api.POST('/api/gigs/import/setlistfm', { body: { username: what.setlistfm } }))
        : postRaw<GigImport>(
            `/api/gigs/import/${what.kind}`,
            what.file,
            what.kind === 'csv' ? 'text/csv' : 'text/calendar',
          ),
    onSuccess: () => qc.invalidateQueries({ queryKey: gigKeys.all }),
  })
}

export function useSetlistFmKey(enabled: boolean) {
  return useQuery({ queryKey: ['admin', 'setlistfm'], enabled, queryFn: () => unwrap(api.GET('/api/admin/setlistfm')) })
}

export function useSetSetlistFmKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (apiKey: string | null) => unwrap(api.PUT('/api/admin/setlistfm', { body: { api_key: apiKey } })),
    onSuccess: (data) => qc.setQueryData(['admin', 'setlistfm'], data),
  })
}

export function useGigStats() {
  return useQuery({ queryKey: [...gigKeys.all, 'stats'], queryFn: () => unwrap(api.GET('/api/gigs/stats')) })
}
