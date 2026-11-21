import { keepPreviousData, useQuery } from '@tanstack/react-query'
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

export type SkipKind = 'track' | 'album' | 'artist'

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
