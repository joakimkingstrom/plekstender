import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, unwrap } from './client'

export type WordKind = 'artist' | 'album' | 'track'

const keys = {
  all: (serverId: number) => ['servers', serverId, 'library', 'stats'] as const,
}

/** Totals, growth over time and albums per decade. */
export function useLibraryStats(serverId: number) {
  return useQuery({
    queryKey: keys.all(serverId),
    staleTime: 5 * 60_000,
    queryFn: () => unwrap(api.GET('/api/servers/{server_id}/library/stats', { params: { path: { server_id: serverId } } })),
  })
}

/** A decade's albums by year, or a year's by month (with its albums). */
export function useReleased(serverId: number, at: { decade: number } | { year: number } | null) {
  return useQuery({
    queryKey: [...keys.all(serverId), 'released', at],
    enabled: at !== null,
    placeholderData: keepPreviousData,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/stats/released', {
          params: { path: { server_id: serverId }, query: at! },
        }),
      ),
  })
}

export function useWords(serverId: number, kind: WordKind) {
  return useQuery({
    queryKey: [...keys.all(serverId), 'words', kind],
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/stats/words', {
          params: { path: { server_id: serverId }, query: { kind, limit: 100 } },
        }),
      ),
  })
}

/** Artists per country (Plex's country, else MusicBrainz's). */
export function useArtistCountries(serverId: number) {
  return useQuery({
    queryKey: [...keys.all(serverId), 'countries'],
    staleTime: 5 * 60_000,
    queryFn: () =>
      unwrap(api.GET('/api/servers/{server_id}/library/stats/countries', { params: { path: { server_id: serverId } } })),
  })
}

export function useCountryArtists(serverId: number, code: string | null) {
  return useQuery({
    queryKey: [...keys.all(serverId), 'countries', code],
    enabled: code !== null,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/stats/countries/{code}', {
          params: { path: { server_id: serverId, code: code! } },
        }),
      ),
  })
}

export type ColourName = 'red' | 'orange' | 'yellow' | 'green' | 'teal' | 'blue' | 'purple' | 'pink' | 'white' | 'grey' | 'black'

/** Covers' main colours by family, and a spectrum strip. */
export function useColours(serverId: number) {
  return useQuery({
    queryKey: [...keys.all(serverId), 'colours'],
    staleTime: 5 * 60_000,
    queryFn: () =>
      unwrap(api.GET('/api/servers/{server_id}/library/stats/colours', { params: { path: { server_id: serverId } } })),
  })
}

export function useColourFamily(serverId: number, name: ColourName | null) {
  return useQuery({
    queryKey: [...keys.all(serverId), 'colours', name],
    enabled: name !== null,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/stats/colours/{name}', {
          params: { path: { server_id: serverId, name: name! } },
        }),
      ),
  })
}
