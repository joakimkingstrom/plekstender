import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type ListenSource = Schemas['ListenOut']['source']

export const SOURCES: Record<ListenSource, string> = {
  lastfm: 'Last.fm',
  plex: 'Plex',
  listenbrainz: 'ListenBrainz',
}

const PAGE = 50

export const listenKeys = {
  all: ['listens'] as const,
  summary: ['listens', 'summary'] as const,
  list: ['listens', 'list'] as const,
  lastfm: ['listens', 'lastfm'] as const,
  plex: ['listens', 'plex'] as const,
  listenbrainz: ['listens', 'listenbrainz'] as const,
  listenbrainzImport: ['listens', 'imports', 'listenbrainz'] as const,
  listenbrainzSend: ['listens', 'listenbrainz-send'] as const,
  lastfmKey: ['admin', 'lastfm'] as const,
}

export function useListenSummary() {
  return useQuery({ queryKey: listenKeys.summary, queryFn: () => unwrap(api.GET('/api/listens/summary')) })
}

/** Narrows the list of listens down, e.g. to what a chart's bar or square stands for. */
export type ListenFilter = {
  start?: string
  end?: string
  weekday?: number
  hour?: number
  tz?: string
  artist?: string[]
  album?: string | null
  counted?: boolean
}

export function useListens(filter: ListenFilter = {}) {
  const query = Object.fromEntries(Object.entries(filter).filter(([, v]) => v !== undefined && v !== null))
  return useInfiniteQuery({
    queryKey: [...listenKeys.list, query],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(api.GET('/api/listens', { params: { query: { ...query, offset: pageParam, limit: PAGE } } })),
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

/** Import status for one source; polls while an import is queued or running. */
function useImportStatus(key: readonly string[], fetch: () => Promise<Schemas['ImportStatusOut']>) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: key,
    queryFn: async () => {
      const data = await fetch()
      const previous = qc.getQueryData<Schemas['ImportStatusOut']>(key)
      if (previous?.active && !data.active) {
        // An import just finished: history and stats changed.
        void qc.invalidateQueries({ queryKey: ['listens', 'summary'] })
        void qc.invalidateQueries({ queryKey: ['listens', 'list'] })
        void qc.invalidateQueries({ queryKey: ['listens', 'stats'] })
      }
      return data
    },
    refetchInterval: (query) => (query.state.data?.active ? 2000 : false),
  })
}

export function useLastFmStatus() {
  return useImportStatus(listenKeys.lastfm, () => unwrap(api.GET('/api/listens/lastfm')))
}

export function useListenBrainzImport() {
  return useImportStatus(listenKeys.listenbrainzImport, () =>
    unwrap(api.GET('/api/listens/imports/listenbrainz')),
  )
}

export function useStartListenBrainzImport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (username: string) =>
      unwrap(api.POST('/api/listens/imports/listenbrainz', { body: { username } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: listenKeys.listenbrainzImport }),
  })
}

/** Sending the whole history to ListenBrainz (a resumable job, like an import). */
export function useListenBrainzSend() {
  return useImportStatus(listenKeys.listenbrainzSend, () => unwrap(api.GET('/api/listens/listenbrainz/send')))
}

export function useStartListenBrainzSend() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/listens/listenbrainz/send')),
    onSuccess: () => qc.invalidateQueries({ queryKey: listenKeys.listenbrainzSend }),
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
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: listenKeys.lastfm })
      void qc.invalidateQueries({ queryKey: listenKeys.listenbrainzImport })
      void qc.invalidateQueries({ queryKey: listenKeys.listenbrainzSend })
    },
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

/** How Plex play history is read for each of the user's servers. */
export function usePlexCapture() {
  return useQuery({
    queryKey: listenKeys.plex,
    queryFn: () => unwrap(api.GET('/api/listens/plex')),
    // Poll while past plays are still being read in.
    refetchInterval: (query) => (query.state.data?.some((s) => !s.backfilled) ? 15_000 : false),
  })
}

export function useListenBrainz() {
  return useQuery({
    queryKey: listenKeys.listenbrainz,
    queryFn: () => unwrap(api.GET('/api/listens/listenbrainz')),
    // While plays are waiting to be sent, keep the counts fresh.
    refetchInterval: (query) => (query.state.data?.pending ? 30_000 : false),
  })
}

export function useConnectListenBrainz() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (token: string) => unwrap(api.PUT('/api/listens/listenbrainz', { body: { token } })),
    onSuccess: (data) => qc.setQueryData(listenKeys.listenbrainz, data),
  })
}

export function useDisconnectListenBrainz() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/listens/listenbrainz')),
    onSuccess: () => qc.invalidateQueries({ queryKey: listenKeys.listenbrainz }),
  })
}
