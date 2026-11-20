import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, postRaw, unwrap, type Schemas } from './client'

export type Playlist = Schemas['PlaylistOut']
export type PlaylistDetail = Schemas['PlaylistDetail']
export type Definition = Schemas['Definition']
export type SeedArtist = Schemas['SeedArtist']
export type SeedTrack = Schemas['SeedTrack']
export type PlaylistIn = Schemas['PlaylistIn']
export type Schedule = NonNullable<PlaylistIn['schedule']>

export const playlistKeys = {
  all: (serverId: number) => ['servers', serverId, 'playlists'] as const,
  one: (serverId: number, id: number) => ['servers', serverId, 'playlists', id] as const,
  search: (serverId: number, q: string) => ['servers', serverId, 'playlist-search', q] as const,
  suggestions: (serverId: number, artists: number[], tracks: number[]) =>
    ['servers', serverId, 'playlist-suggestions', { artists, tracks }] as const,
  preview: (serverId: number, definition: Definition | null) =>
    ['servers', serverId, 'playlist-preview', definition] as const,
}

const building = (p: Playlist) => p.status === 'queued' || p.status === 'running'

export function usePlaylists(serverId: number) {
  return useQuery({
    queryKey: playlistKeys.all(serverId),
    queryFn: () =>
      unwrap(api.GET('/api/servers/{server_id}/playlists', { params: { path: { server_id: serverId } } })),
    // Watch builds finish.
    refetchInterval: (query) => (query.state.data?.some(building) ? 2000 : false),
  })
}

export function usePlaylist(serverId: number, id: number | null) {
  return useQuery({
    queryKey: playlistKeys.one(serverId, id ?? 0),
    enabled: id !== null,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/playlists/{playlist_id}', {
          params: { path: { server_id: serverId, playlist_id: id! } },
        }),
      ),
  })
}

export function usePlaylistSearch(serverId: number, q: string) {
  return useQuery({
    queryKey: playlistKeys.search(serverId, q),
    enabled: q.length >= 2,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/playlists/search', {
          params: { path: { server_id: serverId }, query: { q } },
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  })
}

export async function fetchTagArtists(serverId: number, tagId: number) {
  return unwrap(
    api.GET('/api/servers/{server_id}/playlists/tag-artists', {
      params: { path: { server_id: serverId }, query: { tag_id: tagId } },
    }),
  )
}

export function useSuggestions(serverId: number, artists: number[], tracks: number[]) {
  return useQuery({
    queryKey: playlistKeys.suggestions(serverId, artists, tracks),
    enabled: artists.length + tracks.length > 0,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/playlists/suggestions', {
          params: { path: { server_id: serverId }, query: { artists, tracks } },
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  })
}

export function usePreview(serverId: number, definition: Definition | null) {
  return useQuery({
    queryKey: playlistKeys.preview(serverId, definition),
    enabled: definition !== null,
    queryFn: () =>
      unwrap(
        api.POST('/api/servers/{server_id}/playlists/preview', {
          params: { path: { server_id: serverId } },
          body: definition!,
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
}

export function useSavePlaylist(serverId: number, id: number | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: PlaylistIn) =>
      id === null
        ? unwrap(api.POST('/api/servers/{server_id}/playlists', { params: { path: { server_id: serverId } }, body }))
        : unwrap(
            api.PUT('/api/servers/{server_id}/playlists/{playlist_id}', {
              params: { path: { server_id: serverId, playlist_id: id } },
              body,
            }),
          ),
    onSuccess: () => qc.invalidateQueries({ queryKey: playlistKeys.all(serverId) }),
  })
}

export function useRebuildPlaylist(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.POST('/api/servers/{server_id}/playlists/{playlist_id}/build', {
          params: { path: { server_id: serverId, playlist_id: id } },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: playlistKeys.all(serverId) }),
  })
}

export function useDeletePlaylist(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, fromPlex }: { id: number; fromPlex: boolean }) =>
      unwrap(
        api.DELETE('/api/servers/{server_id}/playlists/{playlist_id}', {
          params: { path: { server_id: serverId, playlist_id: id }, query: { from_plex: fromPlex } },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: playlistKeys.all(serverId) }),
  })
}

export type PlaylistImportPreview = Schemas['PlaylistImportPreviewOut']

/** Read a playlist file (CSV, M3U, JSPF) and match its songs to this server's library. */
export function usePreviewPlaylistImport(serverId: number) {
  return useMutation({
    mutationFn: (file: File) =>
      postRaw<PlaylistImportPreview>(
        `/api/servers/${serverId}/playlists/import/preview?filename=${encodeURIComponent(file.name)}`,
        file,
        file.type || 'application/octet-stream',
      ),
  })
}

export function useCreatePlaylistImport(serverId: number) {
  return useMutation({
    mutationFn: (body: Schemas['PlaylistImportIn']) =>
      unwrap(api.POST('/api/servers/{server_id}/playlists/import', { params: { path: { server_id: serverId } }, body })),
  })
}

/** Read a public Spotify playlist (its link) and match its songs. */
export function usePreviewSpotifyImport(serverId: number) {
  return useMutation({
    mutationFn: (url: string) =>
      unwrap(api.POST('/api/servers/{server_id}/playlists/import/spotify', { params: { path: { server_id: serverId } }, body: { url } })),
  })
}

export function useSpotifyApp(enabled: boolean) {
  return useQuery({ queryKey: ['admin', 'spotify'], enabled, queryFn: () => unwrap(api.GET('/api/admin/spotify')) })
}

export function useSetSpotifyApp() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['SpotifyAppIn']) => unwrap(api.PUT('/api/admin/spotify', { body })),
    onSuccess: (data) => qc.setQueryData(['admin', 'spotify'], data),
  })
}
