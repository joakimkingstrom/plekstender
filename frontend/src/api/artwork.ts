import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { ApiError, api, unwrap, type Schemas } from './client'
import { libraryKeys } from './library'

export type ArtworkStatus = Schemas['ArtworkStatusIn']['status']
export type CandidateSource = 'fanart' | 'plex' | 'search'
export type SourceSpec = Schemas['SourceSpec']
export type Transform = Schemas['Transform']

const PAGE = 60

export const artworkKeys = {
  all: (serverId: number) => ['servers', serverId, 'artwork'] as const,
  artists: (serverId: number, q: string, status: ArtworkStatus | null) =>
    ['servers', serverId, 'artwork', 'artists', { q, status }] as const,
  candidates: (serverId: number, key: number, source: CandidateSource) =>
    ['servers', serverId, 'artwork', 'candidates', key, source] as const,
  fonts: ['artwork', 'fonts'] as const,
  fanartKey: ['admin', 'fanart'] as const,
}

export function useArtworkArtists(serverId: number, q: string, status: ArtworkStatus | null) {
  return useInfiniteQuery({
    queryKey: artworkKeys.artists(serverId, q, status),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/servers/{server_id}/artwork/artists', {
          params: {
            path: { server_id: serverId },
            query: { offset: pageParam, limit: PAGE, ...(q ? { q } : {}), ...(status ? { status } : {}) },
          },
        }),
      ),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0)
      return loaded < last.total ? loaded : undefined
    },
    placeholderData: keepPreviousData,
  })
}

export function useCandidates(serverId: number, key: number, source: CandidateSource, enabled: boolean) {
  return useQuery({
    queryKey: artworkKeys.candidates(serverId, key, source),
    enabled,
    staleTime: 10 * 60_000,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/artwork/artists/{rating_key}/candidates', {
          params: { path: { server_id: serverId, rating_key: key }, query: { source } },
        }),
      ),
  })
}

export function useFonts() {
  return useQuery({ queryKey: artworkKeys.fonts, queryFn: () => unwrap(api.GET('/api/artwork/fonts')), staleTime: Infinity })
}

export function useSetArtworkStatus(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ key, status }: { key: number; status: ArtworkStatus }) =>
      unwrap(
        api.PUT('/api/servers/{server_id}/artwork/artists/{rating_key}/status', {
          params: { path: { server_id: serverId, rating_key: key } },
          body: { status },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: artworkKeys.all(serverId) }),
  })
}

/** POST a raw body (for file uploads), throwing ApiError like the typed client. */
async function postRaw<T>(path: string, body: BodyInit, contentType: string): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'content-type': contentType, 'X-Plekstender': '1' },
    body,
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    throw new ApiError(res.status, typeof data?.detail === 'string' ? data.detail : `Request failed (${res.status})`)
  }
  return data as T
}

export function useUploadImage(serverId: number) {
  return useMutation({
    mutationFn: (file: File) =>
      postRaw<Schemas['UploadOut']>(
        `/api/servers/${serverId}/artwork/uploads`,
        file,
        file.type || 'application/octet-stream',
      ),
  })
}

export function useApplyArtwork(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ key, source, transform }: { key: number; source: SourceSpec; transform: Transform }) =>
      unwrap(
        api.POST('/api/servers/{server_id}/artwork/artists/{rating_key}/apply', {
          params: { path: { server_id: serverId, rating_key: key } },
          body: { source, transform },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: artworkKeys.all(serverId) })
      void qc.invalidateQueries({ queryKey: libraryKeys.library(serverId) })
    },
  })
}

/**
 * A preview of the poster for the current source and settings, as an object URL.
 * Requests are debounced while sliders move, and stale ones are dropped.
 */
export function usePreview(serverId: number, key: number, source: SourceSpec | null, transform: Transform) {
  const [state, setState] = useState<{ url: string | null; loading: boolean; error: string | null }>({
    url: null,
    loading: false,
    error: null,
  })
  const body = source ? JSON.stringify({ source, transform }) : null

  useEffect(() => {
    if (!body) return
    let cancelled = false
    let objectUrl: string | null = null
    const timer = window.setTimeout(async () => {
      setState((s) => ({ ...s, loading: true, error: null }))
      try {
        const res = await fetch(`/api/servers/${serverId}/artwork/artists/${key}/preview`, {
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
        setState({ url: objectUrl, loading: false, error: null })
      } catch (e) {
        if (!cancelled) setState({ url: null, loading: false, error: e instanceof Error ? e.message : 'Preview failed' })
      }
    }, 250)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [serverId, key, body])

  return body ? state : { url: null, loading: false, error: null }
}

export function useFanartKey(enabled: boolean) {
  return useQuery({ queryKey: artworkKeys.fanartKey, enabled, queryFn: () => unwrap(api.GET('/api/admin/fanart')) })
}

export function useSetFanartKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (apiKey: string | null) => unwrap(api.PUT('/api/admin/fanart', { body: { api_key: apiKey } })),
    onSuccess: (data) => {
      qc.setQueryData(artworkKeys.fanartKey, data)
      void qc.invalidateQueries({ queryKey: ['servers'] })
    },
  })
}

export type ArtworkJob = Schemas['ArtworkJobOut']

/** Recent artwork jobs; polls while one is queued or running, and refreshes the list after. */
export function useArtworkJobs(serverId: number) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: [...artworkKeys.all(serverId), 'jobs'],
    queryFn: async () => {
      const jobs = await unwrap(
        api.GET('/api/servers/{server_id}/artwork/jobs', { params: { path: { server_id: serverId } } }),
      )
      const previous = qc.getQueryData<ArtworkJob[]>([...artworkKeys.all(serverId), 'jobs'])
      const wasActive = previous?.some((j) => j.status === 'queued' || j.status === 'running')
      if (wasActive && !jobs.some((j) => j.status === 'queued' || j.status === 'running')) {
        void qc.invalidateQueries({ queryKey: [...artworkKeys.all(serverId), 'artists'] })
        void qc.invalidateQueries({ queryKey: libraryKeys.library(serverId) })
      }
      return jobs
    },
    refetchInterval: (query) =>
      query.state.data?.some((j) => j.status === 'queued' || j.status === 'running') ? 2000 : false,
  })
}

export function useStartFanartJob(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['FanartJobIn']) =>
      unwrap(
        api.POST('/api/servers/{server_id}/artwork/jobs/fanart', { params: { path: { server_id: serverId } }, body }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...artworkKeys.all(serverId), 'jobs'] }),
  })
}

export function useFolderSettings(serverId: number) {
  return useQuery({
    queryKey: [...artworkKeys.all(serverId), 'folder'],
    queryFn: () =>
      unwrap(api.GET('/api/servers/{server_id}/artwork/folder', { params: { path: { server_id: serverId } } })),
  })
}

export function useSetFolderSettings(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['FolderSettingsIn']) =>
      unwrap(api.PUT('/api/servers/{server_id}/artwork/folder', { params: { path: { server_id: serverId } }, body })),
    onSuccess: (data) => qc.setQueryData([...artworkKeys.all(serverId), 'folder'], data),
  })
}
