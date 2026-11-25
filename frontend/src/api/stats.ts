import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './client'

/** The viewer's IANA time zone, so days and hours in stats are local. */
export function localTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export type TopBy = 'listens' | 'time'

export function useStatsOverview(range: string, serverId: number | null, by: TopBy = 'listens') {
  const tz = localTimeZone()
  return useQuery({
    queryKey: ['listens', 'stats', 'overview', range, tz, serverId, by],
    queryFn: () =>
      unwrap(
        api.GET('/api/stats/overview', {
          params: { query: { range, tz, by, ...(serverId !== null ? { server_id: serverId } : {}) } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

export function useStatsActivity(range: string) {
  const tz = localTimeZone()
  return useQuery({
    queryKey: ['listens', 'stats', 'activity', range, tz],
    queryFn: () => unwrap(api.GET('/api/stats/activity', { params: { query: { range, tz } } })),
    placeholderData: keepPreviousData,
  })
}

export type SkipKind = 'track' | 'artist'

export function useSkips(serverId: number | null, range: string, kind: SkipKind) {
  const tz = localTimeZone()
  return useQuery({
    queryKey: ['listens', 'stats', 'skips', serverId, range, tz, kind],
    enabled: serverId !== null,
    queryFn: () =>
      unwrap(api.GET('/api/stats/skips', { params: { query: { server_id: serverId!, range, tz, kind } } })),
    placeholderData: keepPreviousData,
  })
}

export function useSkipRate(serverId: number | null, range: string) {
  const tz = localTimeZone()
  return useQuery({
    queryKey: ['listens', 'stats', 'skip-rate', serverId, range, tz],
    enabled: serverId !== null,
    queryFn: () => unwrap(api.GET('/api/stats/skip-rate', { params: { query: { server_id: serverId!, range, tz } } })),
    placeholderData: keepPreviousData,
  })
}

/** What you played on today's date in earlier years. */
export function useOnThisDay(serverId: number | null) {
  const tz = localTimeZone()
  return useQuery({
    queryKey: ['listens', 'stats', 'on-this-day', serverId, tz],
    queryFn: () =>
      unwrap(
        api.GET('/api/stats/on-this-day', {
          params: { query: { tz, ...(serverId !== null ? { server_id: serverId } : {}) } },
        }),
      ),
  })
}

export type TasteKind = 'genre' | 'style' | 'mood'

/** Genres (or styles, or moods) and release decades per year you listened. */
export function useTaste(serverId: number | null, kind: TasteKind = 'genre') {
  const tz = localTimeZone()
  return useQuery({
    queryKey: ['listens', 'stats', 'taste', serverId, tz, kind],
    enabled: serverId !== null,
    queryFn: () => unwrap(api.GET('/api/stats/taste', { params: { query: { server_id: serverId!, tz, kind } } })),
    placeholderData: keepPreviousData,
  })
}

/** Songs whose ratings and plays disagree. Under the library key (libraryKeys.library, not
 * imported: library.ts imports this file), so rating one refreshes it. */
export function useRediscover(serverId: number, includeHidden = false) {
  return useQuery({
    queryKey: ['servers', serverId, 'library', 'rediscover', includeHidden],
    queryFn: () =>
      unwrap(api.GET('/api/stats/rediscover', { params: { query: { server_id: serverId, include_hidden: includeHidden } } })),
    placeholderData: keepPreviousData,
  })
}

/** Hide a song from Rediscover (and the forgotten favourites playlist), or show it again. */
export function useHideSong(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ ratingKey, hide }: { ratingKey: number; hide: boolean }) => {
      const params = { path: { rating_key: ratingKey }, query: { server_id: serverId } }
      return unwrap(
        hide
          ? api.PUT('/api/stats/rediscover/hidden/{rating_key}', { params })
          : api.DELETE('/api/stats/rediscover/hidden/{rating_key}', { params }),
      )
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['servers', serverId, 'library', 'rediscover'] }),
  })
}

/** An area of the listens map: the whole world, a continent or a country. */
export type MapArea =
  | { level: 'world' }
  | { level: 'continent'; continent: string }
  | { level: 'country'; continent: string | null; country: string }

export function usePlaces(range: string) {
  const tz = localTimeZone()
  return useQuery({
    queryKey: ['listens', 'stats', 'places', range, tz],
    queryFn: () => unwrap(api.GET('/api/stats/places', { params: { query: { range, tz } } })),
    placeholderData: keepPreviousData,
  })
}

export function usePlaceDetail(range: string, area: MapArea, serverId: number | null) {
  const tz = localTimeZone()
  const continent = area.level === 'continent' ? area.continent : undefined
  const country = area.level === 'country' ? area.country : undefined
  return useQuery({
    queryKey: ['listens', 'stats', 'places', range, tz, continent ?? null, country ?? null, serverId],
    queryFn: () =>
      unwrap(
        api.GET('/api/stats/places/detail', {
          params: { query: { range, tz, continent, country, ...(serverId !== null ? { server_id: serverId } : {}) } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}
