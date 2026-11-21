import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, postRaw, unwrap, type Schemas } from './client'
import { liveKeys } from './live'

export type Gig = Schemas['GigOut']
export type GigIn = Schemas['GigIn']

export type GigPlace = Schemas['GigPlace']

/** What Gigs → Been to is narrowed to ('' / null / false: everything). */
export type GigFilters = {
  year: number | null
  artist: string
  country: string
  city: string
  venue: string
  unplaced: boolean
}

export const NO_GIG_FILTERS: GigFilters = { year: null, artist: '', country: '', city: '', venue: '', unplaced: false }

export const gigKeys = {
  all: ['gigs'] as const,
  list: (filters: GigFilters) => [...gigKeys.all, 'list', filters] as const,
}

export function useGigs(filters: GigFilters = NO_GIG_FILTERS) {
  const { year, artist, country, city, venue, unplaced } = filters
  return useQuery({
    queryKey: gigKeys.list(filters),
    queryFn: () =>
      unwrap(
        api.GET('/api/gigs', {
          params: {
            query: {
              year: year ?? undefined,
              artist: artist || undefined,
              country: country || undefined,
              city: city || undefined,
              venue: venue || undefined,
              unplaced: unplaced || undefined,
            },
          },
        }),
      ),
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

export type SetlistLookup = Schemas['SetlistLookupOut']
export type SetlistSearch = { artist: string; year: string; city: string; page: number }

/** Setlists on setlist.fm by artist, year and town (null: not searched yet). */
export function useSetlistSearch(search: SetlistSearch | null) {
  return useQuery({
    queryKey: [...gigKeys.all, 'setlistfm', search],
    enabled: search !== null,
    queryFn: () =>
      unwrap(
        api.GET('/api/gigs/setlistfm/search', {
          params: {
            query: {
              artist: search!.artist || undefined,
              year: search!.year ? Number(search!.year) : undefined,
              city: search!.city || undefined,
              page: search!.page,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
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

export type GigPlaceFound = Schemas['GigPlaceFound']
export type PlaceSearch = { q: string; country: string | null }

/** Places on OpenStreetMap for a name, to pick a gig's place by hand (searched when asked:
 * the map search is shared, at most a request a second). */
export function useGigPlaceSearch(search: PlaceSearch | null) {
  return useQuery({
    queryKey: [...gigKeys.all, 'places', search],
    enabled: search !== null,
    staleTime: Infinity,
    queryFn: () =>
      unwrap(api.GET('/api/gigs/places', { params: { query: { q: search!.q, country: search!.country || undefined } } })),
  })
}
