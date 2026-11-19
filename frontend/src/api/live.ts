import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type LiveSettings = Schemas['LiveSettingsOut']
export type LiveSettingsIn = Schemas['LiveSettingsIn']
export type LiveShow = Schemas['LiveShowOut']
export type Place = Schemas['PlaceOut']
export type ShowFilters = { artist?: string; venue?: string; from?: string; to?: string; starred?: boolean }

export const liveKeys = {
  all: ['live'] as const,
  settings: () => [...liveKeys.all, 'settings'] as const,
  shows: (filters: ShowFilters) => [...liveKeys.all, 'shows', filters] as const,
}

const busy = (s: LiveSettings | undefined) => s?.refresh_status === 'queued' || s?.refresh_status === 'running'

export function useLiveSettings() {
  return useQuery({
    queryKey: liveKeys.settings(),
    queryFn: () => unwrap(api.GET('/api/live/settings')),
    // Watch a refresh until the worker has finished it.
    refetchInterval: (query) => (busy(query.state.data) ? 3000 : false),
  })
}

export function useLiveShows(filters: ShowFilters, refreshing: boolean) {
  return useQuery({
    queryKey: liveKeys.shows(filters),
    queryFn: () =>
      unwrap(
        api.GET('/api/live/shows', {
          params: {
            query: {
              artist: filters.artist || undefined,
              venue: filters.venue || undefined,
              from: filters.from,
              to: filters.to,
              starred: filters.starred || undefined,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: refreshing ? 3000 : false,
  })
}

export function useSaveLiveSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: LiveSettingsIn) => unwrap(api.PUT('/api/live/settings', { body })),
    onSuccess: (data) => {
      qc.setQueryData(liveKeys.settings(), data)
      qc.invalidateQueries({ queryKey: [...liveKeys.all, 'shows'] })
    },
  })
}

export function useForgetLiveSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/live/settings')),
    onSuccess: () => qc.invalidateQueries({ queryKey: liveKeys.all }),
  })
}

export function useRefreshLive() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/live/refresh')),
    onSuccess: (data) => qc.setQueryData(liveKeys.settings(), data),
  })
}

export function usePlaces(q: string) {
  return useQuery({
    queryKey: [...liveKeys.all, 'places', q],
    queryFn: () => unwrap(api.GET('/api/live/places', { params: { query: { q } } })),
    enabled: q.length >= 2,
    staleTime: 10 * 60_000,
  })
}

export function useJamBaseKey(enabled: boolean, check = false) {
  return useQuery({
    queryKey: ['admin', 'jambase', check],
    enabled,
    queryFn: () => unwrap(api.GET('/api/admin/jambase', { params: { query: { check } } })),
  })
}

export function useSetJamBaseKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (apiKey: string | null) => unwrap(api.PUT('/api/admin/jambase', { body: { api_key: apiKey } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin', 'jambase'] })
      qc.invalidateQueries({ queryKey: liveKeys.settings() })
    },
  })
}

export function useStarShow() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ ids, starred }: { ids: number[]; starred: boolean }) =>
      unwrap(api.PUT('/api/live/stars', { body: { ids, starred } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...liveKeys.all, 'shows'] }),
  })
}

export function useCalendarAddress(enabled: boolean) {
  return useQuery({
    queryKey: [...liveKeys.all, 'calendar'],
    enabled,
    queryFn: () => unwrap(api.GET('/api/live/calendar')),
    staleTime: Infinity,
  })
}

export function useResetCalendar() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/live/calendar/reset')),
    onSuccess: (data) => qc.setQueryData([...liveKeys.all, 'calendar'], data),
  })
}

export function useTicketmasterKey(enabled: boolean) {
  return useQuery({
    queryKey: ['admin', 'ticketmaster'],
    enabled,
    queryFn: () => unwrap(api.GET('/api/admin/ticketmaster')),
  })
}

export function useSetTicketmasterKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (apiKey: string | null) =>
      unwrap(api.PUT('/api/admin/ticketmaster', { body: { api_key: apiKey } })),
    onSuccess: (data) => {
      qc.setQueryData(['admin', 'ticketmaster'], data)
      qc.invalidateQueries({ queryKey: liveKeys.settings() })
    },
  })
}

/** How many artists were played in a period (null: all time), for the settings preview. */
export function useListenedCount(periodMonths: number | null) {
  return useQuery({
    queryKey: [...liveKeys.all, 'listened', periodMonths],
    queryFn: () =>
      unwrap(api.GET('/api/live/listened', { params: { query: { period_months: periodMonths ?? undefined } } })),
    placeholderData: keepPreviousData,
  })
}

export function useGotTicket() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ ids, gotTicket }: { ids: number[]; gotTicket: boolean }) =>
      unwrap(api.PUT('/api/live/tickets', { body: { ids, got_ticket: gotTicket } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...liveKeys.all, 'shows'] }),
  })
}

export type LiveFeed = Schemas['LiveFeedOut']

export function useLiveFeeds() {
  return useQuery({
    queryKey: [...liveKeys.all, 'feeds'],
    queryFn: () => unwrap(api.GET('/api/live/feeds')),
  })
}

export function useAddLiveFeed() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (url: string) => unwrap(api.POST('/api/live/feeds', { body: { url } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...liveKeys.all, 'feeds'] })
      qc.invalidateQueries({ queryKey: liveKeys.settings() })
    },
  })
}

export function useRemoveLiveFeed() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE('/api/live/feeds/{feed_id}', { params: { path: { feed_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: liveKeys.all }),
  })
}

export function useDataThistle(enabled: boolean) {
  return useQuery({
    queryKey: ['admin', 'datathistle'],
    enabled,
    queryFn: () => unwrap(api.GET('/api/admin/datathistle')),
  })
}

export function useSetDataThistle() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (tokens: { access_token: string; refresh_token: string } | null) =>
      tokens
        ? unwrap(api.PUT('/api/admin/datathistle', { body: tokens }))
        : unwrap(api.DELETE('/api/admin/datathistle')),
    onSuccess: (data) => {
      qc.setQueryData(['admin', 'datathistle'], data)
      qc.invalidateQueries({ queryKey: liveKeys.settings() })
    },
  })
}
