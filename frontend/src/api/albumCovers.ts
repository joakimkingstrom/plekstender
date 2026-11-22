import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from './client'
import type { paths } from './schema'
import type { SourceSpec } from './artwork'

export type AlbumCover = Schemas['AlbumCoverOut']
// The list's filter, as the API defines it (`show`).
export type CoverFilter = NonNullable<
  NonNullable<paths['/api/servers/{server_id}/artwork/albums']['get']['parameters']['query']>['show']
>
export type CoverSource = 'coverartarchive' | 'deezer' | 'itunes' | 'tidal' | 'fanart' | 'plex' | 'search'

const keys = {
  all: (serverId: number) => ['servers', serverId, 'artwork', 'albums'] as const,
  list: (serverId: number, show: CoverFilter, q: string) => ['servers', serverId, 'artwork', 'albums', 'list', { show, q }] as const,
  candidates: (serverId: number, key: number, source: CoverSource, q: string) =>
    ['servers', serverId, 'artwork', 'albums', 'candidates', key, source, q] as const,
  tidal: ['admin', 'tidal'] as const,
}

/** Albums and their covers: by default the ones most in need of a better cover. */
export function useAlbumCovers(serverId: number, show: CoverFilter, q: string) {
  return useInfiniteQuery({
    queryKey: keys.list(serverId, show, q),
    initialPageParam: 0,
    placeholderData: keepPreviousData,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/servers/{server_id}/artwork/albums', {
          params: { path: { server_id: serverId }, query: { show, q: q || undefined, offset: pageParam, limit: 60 } },
        }),
      ),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0)
      return loaded < last.total ? loaded : undefined
    },
    // Covers still being measured: look again in a bit.
    refetchInterval: (query) => ((query.state.data?.pages[0]?.counts.unchecked ?? 0) > 0 ? 15_000 : false),
  })
}

/** The sources usable here (Tidal and fanart.tv once set up, web search while it answers). */
export function useCoverSources(serverId: number) {
  return useQuery({
    queryKey: [...keys.all(serverId), 'sources'],
    staleTime: 5 * 60_000,
    queryFn: () =>
      unwrap(api.GET('/api/servers/{server_id}/artwork/albums/sources', { params: { path: { server_id: serverId } } })),
  })
}

export function useCoverCandidates(serverId: number, key: number, source: CoverSource, q = '') {
  return useQuery({
    queryKey: keys.candidates(serverId, key, source, q),
    staleTime: 10 * 60_000,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/artwork/albums/{rating_key}/candidates', {
          params: { path: { server_id: serverId, rating_key: key }, query: { source, q: q || undefined } },
        }),
      ),
  })
}

type Preview = { url: string | null; width: number | null; height: number | null; loading: boolean; error: string | null }
const EMPTY: Preview = { url: null, width: null, height: null, loading: false, error: null }

/** The cover as it would be saved, with its size. */
export function useCoverPreview(serverId: number, key: number, source: SourceSpec | null, square: boolean) {
  const [state, setState] = useState<Preview>(EMPTY)
  const body = source ? JSON.stringify({ source, square }) : null
  useEffect(() => {
    if (!body) return
    let cancelled = false
    let objectUrl: string | null = null
    const timer = window.setTimeout(async () => {
      setState((s) => ({ ...s, loading: true, error: null }))
      try {
        const res = await fetch(`/api/servers/${serverId}/artwork/albums/${key}/preview`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'content-type': 'application/json', 'X-Plekstender': '1' },
          body,
        })
        if (!res.ok) {
          const detail = await res.json().catch(() => null)
          throw new Error(typeof detail?.detail === 'string' ? detail.detail : `Preview failed (${res.status})`)
        }
        const blob = await res.blob()
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        const size = (name: string) => Number(res.headers.get(name)) || null
        setState({ url: objectUrl, width: size('x-image-width'), height: size('x-image-height'), loading: false, error: null })
      } catch (e) {
        if (!cancelled) setState({ ...EMPTY, error: e instanceof Error ? e.message : 'Preview failed' })
      }
    }, 150)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [serverId, key, body])
  return body ? state : EMPTY
}

export function useApplyCover(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ key, source, square }: { key: number; source: SourceSpec; square: boolean }) =>
      unwrap(
        api.POST('/api/servers/{server_id}/artwork/albums/{rating_key}/apply', {
          params: { path: { server_id: serverId, rating_key: key } },
          body: { source, square },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all(serverId) }),
  })
}

export type BestOutcome = Schemas['AlbumBestOut']
export type BestSource = NonNullable<Schemas['BestCoversJobIn']['sources']>[number]

/** The best match (this very album, at least 1000 × 1000) found and saved to Plex. */
export function useApplyBest(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (key: number) =>
      unwrap(
        api.POST('/api/servers/{server_id}/artwork/albums/{rating_key}/best', {
          params: { path: { server_id: serverId, rating_key: key } },
        }),
      ),
    onSuccess: (out) => {
      if (out.outcome === 'applied') void qc.invalidateQueries({ queryKey: keys.all(serverId) })
    },
  })
}

/** The best match for every album the list shows (a background artwork job). */
export function useStartBestCovers(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['BestCoversJobIn']) =>
      unwrap(api.POST('/api/servers/{server_id}/artwork/albums/best', { params: { path: { server_id: serverId } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['servers', serverId, 'artwork', 'jobs'] }),
  })
}

export function useKeepCover(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ key, kept }: { key: number; kept: boolean }) =>
      unwrap(
        api.PUT('/api/servers/{server_id}/artwork/albums/{rating_key}/keep', {
          params: { path: { server_id: serverId, rating_key: key } },
          body: { kept },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all(serverId) }),
  })
}

export function useTidal(enabled: boolean) {
  return useQuery({ queryKey: keys.tidal, enabled, queryFn: () => unwrap(api.GET('/api/admin/tidal')) })
}

export function useSetTidal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['TidalIn']) => unwrap(api.PUT('/api/admin/tidal', { body })),
    onSuccess: (data) => qc.setQueryData(keys.tidal, data),
  })
}
