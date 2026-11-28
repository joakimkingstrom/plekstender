import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { api, unwrap, type Schemas } from './client'
import { useServers } from './hooks'
import { localTimeZone } from './stats'

export type ArtistSort = 'title' | 'popularity' | 'plays' | 'rating' | 'added'
export type TagKind = 'genre' | 'mood' | 'style' | 'country'
export type TagSort = 'plays' | 'rating' | 'year' | 'name'

export const libraryKeys = {
  sync: (serverId: number) => ['servers', serverId, 'sync'] as const,
  library: (serverId: number) => ['servers', serverId, 'library'] as const,
  artists: (serverId: number, q: string, sort: ArtistSort, tag: number | null) =>
    ['servers', serverId, 'library', 'artists', { q, sort, tag }] as const,
  tags: (serverId: number, kind: TagKind) => ['servers', serverId, 'library', 'tags', kind] as const,
  tag: (serverId: number, id: number, sort: TagSort) => ['servers', serverId, 'library', 'tag', id, sort] as const,
  artist: (serverId: number, key: number) => ['servers', serverId, 'library', 'artist', key] as const,
  album: (serverId: number, key: number) => ['servers', serverId, 'library', 'album', key] as const,
}

const ACTIVE_SERVER_KEY = 'plekstender.activeServer'

function readStoredServer(): number | null {
  try {
    const raw = localStorage.getItem(ACTIVE_SERVER_KEY)
    return raw ? Number(raw) : null
  } catch {
    return null
  }
}

// The picked server, shared by every component that asks (the Library header's picker and
// the page under it), and remembered in localStorage.
let storedServer: number | null = readStoredServer()
const serverListeners = new Set<() => void>()
function subscribeServer(listener: () => void) {
  serverListeners.add(listener)
  return () => {
    serverListeners.delete(listener)
  }
}
function setStored(id: number) {
  storedServer = id
  for (const listener of serverListeners) listener()
}

/** The server the library pages show: the last one picked, else the first with a library. */
export function useActiveServer() {
  const servers = useServers()
  const stored = useSyncExternalStore(subscribeServer, () => storedServer)
  const list = servers.data ?? []
  const active =
    list.find((s) => s.id === stored) ??
    list.find((s) => s.music_section_keys.length > 0) ??
    list[0] ??
    null

  const choose = (id: number) => {
    setStored(id)
    try {
      localStorage.setItem(ACTIVE_SERVER_KEY, String(id))
    } catch {
      // storage unavailable; the choice lasts for this page only
    }
  }
  return { servers, active, choose }
}

/** Plex artwork through the backend's image proxy (which adds the user's own token). */
export function imageUrl(serverId: number, path: string | null | undefined, size = 256) {
  if (!path) return null
  const params = new URLSearchParams({ path, size: String(size) })
  return `/api/servers/${serverId}/image?${params}`
}

export function useSyncStatus(serverId: number | null) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: libraryKeys.sync(serverId ?? 0),
    enabled: serverId !== null,
    queryFn: async () => {
      const data = await unwrap(
        api.GET('/api/servers/{server_id}/sync', { params: { path: { server_id: serverId! } } }),
      )
      // A sync just finished: library data may have changed.
      const previous = qc.getQueryData<Schemas['SyncStatusOut']>(libraryKeys.sync(serverId!))
      if (previous?.active && !data.active) {
        void qc.invalidateQueries({ queryKey: libraryKeys.library(serverId!) })
      }
      return data
    },
    // Poll quickly while a sync runs, slowly otherwise (scheduled syncs happen too).
    refetchInterval: (query) => (query.state.data?.active ? 2000 : 60_000),
  })
}

export function useStartSync(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (kind: 'full' | 'incremental') =>
      unwrap(
        api.POST('/api/servers/{server_id}/sync', {
          params: { path: { server_id: serverId } },
          body: { kind },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: libraryKeys.sync(serverId) }),
  })
}

const PAGE = 60

export function useArtists(serverId: number, q: string, sort: ArtistSort, tag: number | null) {
  return useInfiniteQuery({
    queryKey: libraryKeys.artists(serverId, q, sort, tag),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/artists', {
          params: {
            path: { server_id: serverId },
            query: { q: q || undefined, sort, tag: tag ?? undefined, offset: pageParam, limit: PAGE },
          },
        }),
      ),
    getNextPageParam: (last) =>
      last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined,
    placeholderData: keepPreviousData,
  })
}

export function useTags(serverId: number, kind: TagKind) {
  return useQuery({
    queryKey: libraryKeys.tags(serverId, kind),
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/tags', {
          params: { path: { server_id: serverId }, query: { kind, limit: 40 } },
        }),
      ),
    staleTime: 5 * 60_000,
  })
}

/** The artists before and after one, by name (stepping through artist pages). */
export function useArtistNeighbours(serverId: number, key: number) {
  return useQuery({
    queryKey: [...libraryKeys.artist(serverId, key), 'neighbours'],
    staleTime: 5 * 60_000,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/artists/{rating_key}/neighbours', {
          params: { path: { server_id: serverId, rating_key: key } },
        }),
      ),
  })
}

export function useArtist(serverId: number, key: number) {
  return useQuery({
    queryKey: libraryKeys.artist(serverId, key),
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/artists/{rating_key}', {
          params: { path: { server_id: serverId, rating_key: key }, query: { tz: localTimeZone() } },
        }),
      ),
  })
}

/** Every release of a song by the same artist (remasters, live, demos…), oldest first. */
export function useSongVersions(serverId: number, key: number | null) {
  return useQuery({
    queryKey: [...libraryKeys.library(serverId), 'versions', key],
    enabled: key !== null,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/tracks/{rating_key}/versions', {
          params: { path: { server_id: serverId, rating_key: key! } },
        }),
      ),
  })
}

export function useAlbum(serverId: number, key: number) {
  return useQuery({
    queryKey: libraryKeys.album(serverId, key),
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/albums/{rating_key}', {
          params: { path: { server_id: serverId, rating_key: key }, query: { tz: localTimeZone() } },
        }),
      ),
  })
}

/** A genre's, style's or mood's page: its artists and albums, and related tags. */
export function useTag(serverId: number, id: number, sort: TagSort) {
  return useQuery({
    queryKey: libraryKeys.tag(serverId, id, sort),
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/library/tags/{tag_id}', {
          params: { path: { server_id: serverId, tag_id: id }, query: { sort } },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}
