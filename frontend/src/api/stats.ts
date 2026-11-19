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
