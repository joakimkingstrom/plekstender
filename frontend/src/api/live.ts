import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type LiveSettings = Schemas['LiveSettingsOut']
export type LiveSettingsIn = Schemas['LiveSettingsIn']
export type LiveShow = Schemas['LiveShowOut']
export type Place = Schemas['PlaceOut']
export type ShowFilters = { artist?: string; from?: string; to?: string }

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
          params: { query: { artist: filters.artist || undefined, from: filters.from, to: filters.to } },
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
