import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type ListenSource = Schemas['ListenOut']['source']

const PAGE = 50

export const listenKeys = {
  all: ['listens'] as const,
  summary: ['listens', 'summary'] as const,
  list: ['listens', 'list'] as const,
  lastfm: ['listens', 'lastfm'] as const,
  lastfmKey: ['admin', 'lastfm'] as const,
}

export function useListenSummary() {
  return useQuery({ queryKey: listenKeys.summary, queryFn: () => unwrap(api.GET('/api/listens/summary')) })
}

export function useListens() {
  return useInfiniteQuery({
    queryKey: listenKeys.list,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(api.GET('/api/listens', { params: { query: { offset: pageParam, limit: PAGE } } })),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0)
      return loaded < last.total ? loaded : undefined
    },
  })
}

export function useDeleteListen() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE('/api/listens/{listen_id}', { params: { path: { listen_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: listenKeys.all }),
  })
}

export function useDeleteListens() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (source: ListenSource | null) =>
      unwrap(api.DELETE('/api/listens', { params: { query: source ? { source } : {} } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: listenKeys.all }),
  })
}

/** Last.fm import status; polls while an import is queued or running. */
export function useLastFmStatus() {
  const qc = useQueryClient()
  return useQuery({
    queryKey: listenKeys.lastfm,
    queryFn: async () => {
      const data = await unwrap(api.GET('/api/listens/lastfm'))
      const previous = qc.getQueryData<Schemas['LastFmStatusOut']>(listenKeys.lastfm)
      if (previous?.active && !data.active) {
        void qc.invalidateQueries({ queryKey: ['listens', 'summary'] })
        void qc.invalidateQueries({ queryKey: ['listens', 'list'] })
      }
      return data
    },
    refetchInterval: (query) => (query.state.data?.active ? 2000 : false),
  })
}

export function useStartLastFmImport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (username: string) => unwrap(api.POST('/api/listens/lastfm', { body: { username } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: listenKeys.lastfm }),
  })
}

export function useResumeImport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.POST('/api/listens/imports/{import_id}/resume', { params: { path: { import_id: id } } }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: listenKeys.lastfm }),
  })
}

export function useLastFmKey(enabled: boolean) {
  return useQuery({
    queryKey: listenKeys.lastfmKey,
    enabled,
    queryFn: () => unwrap(api.GET('/api/admin/lastfm')),
  })
}

export function useSetLastFmKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (apiKey: string | null) => unwrap(api.PUT('/api/admin/lastfm', { body: { api_key: apiKey } })),
    onSuccess: (data) => {
      qc.setQueryData(listenKeys.lastfmKey, data)
      void qc.invalidateQueries({ queryKey: listenKeys.lastfm })
    },
  })
}
