import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type ReleaseKind = Schemas['ReleaseOut']['kind']
export type Release = Schemas['ReleaseOut']
export type NewRelease = Schemas['NewReleaseOut']

const PAGE = 20

const keys = {
  all: (serverId: number) => ['servers', serverId, 'releases'] as const,
}

export function useNewReleases(serverId: number, kinds: ReleaseKind[], includeHidden: boolean) {
  return useQuery({
    queryKey: [...keys.all(serverId), 'new', kinds, includeHidden],
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/releases/new', {
          params: { path: { server_id: serverId }, query: { kind: kinds, include_hidden: includeHidden } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

export function useMissingReleases(serverId: number, kinds: ReleaseKind[], q: string, includeHidden: boolean) {
  return useInfiniteQuery({
    queryKey: [...keys.all(serverId), 'missing', kinds, q, includeHidden],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/servers/{server_id}/releases/missing', {
          params: {
            path: { server_id: serverId },
            query: { kind: kinds, include_hidden: includeHidden, offset: pageParam, limit: PAGE, ...(q ? { q } : {}) },
          },
        }),
      ),
    getNextPageParam: (last, pages) => {
      const seen = pages.reduce((n, p) => n + p.artists.length, 0)
      return seen < last.total_artists ? seen : undefined
    },
    placeholderData: keepPreviousData,
  })
}

/** Hide a release from these lists (or show it again). */
export function useHideRelease(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ mbid, hide }: { mbid: string; hide: boolean }) => {
      const params = { path: { server_id: serverId, mbid } }
      return unwrap(
        hide
          ? api.PUT('/api/servers/{server_id}/releases/hidden/{mbid}', { params })
          : api.DELETE('/api/servers/{server_id}/releases/hidden/{mbid}', { params }),
      )
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all(serverId) }),
  })
}

/** "2019-05-21" → "21 May 2019", "2019-05" → "May 2019", "2019" stays. */
export function formatReleaseDate(value: string | null | undefined): string {
  if (!value) return 'Date unknown'
  const [y, m, d] = value.split('-').map(Number)
  if (!m) return String(y)
  const date = new Date(y, m - 1, d || 1)
  return new Intl.DateTimeFormat(undefined, d ? { day: 'numeric', month: 'short', year: 'numeric' } : { month: 'long', year: 'numeric' }).format(date)
}
