import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type LidarrAdmin = Schemas['LidarrAdminOut']
export type LidarrOptions = Schemas['LidarrOptionsOut']
export type LidarrSetup = Schemas['LidarrIn']

export const lidarrKeys = {
  admin: ['admin', 'lidarr'] as const,
  status: ['lidarr', 'status'] as const,
  states: ['lidarr', 'states'] as const,
}

/** Whether Lidarr's buttons show for you (connected, and you may use it). */
export function useLidarrAvailable(): boolean {
  const status = useQuery({
    queryKey: lidarrKeys.status,
    staleTime: 5 * 60_000,
    queryFn: () => unwrap(api.GET('/api/lidarr')),
  })
  return status.data?.available ?? false
}

/** Admins: the saved connection. */
export function useLidarrAdmin() {
  return useQuery({ queryKey: lidarrKeys.admin, queryFn: () => unwrap(api.GET('/api/admin/lidarr')) })
}

/** Admins: what the Lidarr at an address offers (the saved key when none is given). */
export function checkLidarr(url: string, apiKey: string | null) {
  return unwrap(api.POST('/api/admin/lidarr/check', { body: { url, api_key: apiKey } }))
}

export function useSaveLidarr() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: LidarrSetup) => unwrap(api.PUT('/api/admin/lidarr', { body })),
    onSuccess: (data) => {
      qc.setQueryData(lidarrKeys.admin, data)
      void qc.invalidateQueries({ queryKey: lidarrKeys.status })
    },
  })
}

export function useDisconnectLidarr() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/admin/lidarr')),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: lidarrKeys.admin })
      void qc.invalidateQueries({ queryKey: lidarrKeys.status })
    },
  })
}

export type LidarrAlbumState = Schemas['LidarrAlbumOut']['state']
export type LidarrRelease = Schemas['LidarrReleaseOut']

/** Whether each album (release group MBID) is in Lidarr; only asked when Lidarr is available. */
export function useLidarrStates(mbids: string[], enabled: boolean) {
  const sorted = [...new Set(mbids)].sort()
  return useQuery({
    queryKey: [...lidarrKeys.states, sorted],
    enabled: enabled && sorted.length > 0,
    staleTime: 60_000,
    queryFn: () => unwrap(api.POST('/api/lidarr/albums/states', { body: { mbids: sorted } })),
  })
}

/** Add an album to Lidarr, monitored (and with `search`, look for it now). */
export function useAddLidarrAlbum() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { mbid: string; search: boolean }) => unwrap(api.POST('/api/lidarr/albums', { body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: lidarrKeys.states }),
  })
}

/** What Lidarr's indexers have for an album in Lidarr (slow: it asks them all). */
export function useLidarrReleases(albumId: number | null) {
  return useQuery({
    queryKey: ['lidarr', 'releases', albumId],
    enabled: albumId !== null,
    staleTime: 5 * 60_000,
    retry: false,
    queryFn: () => unwrap(api.GET('/api/lidarr/albums/{album_id}/releases', { params: { path: { album_id: albumId! } } })),
  })
}

export function useGrabRelease() {
  return useMutation({
    mutationFn: (body: { guid: string; indexer_id: number }) => unwrap(api.POST('/api/lidarr/releases', { body })),
  })
}

export type LidarrArtist = Schemas['LidarrArtistOut']
export type ArtistMonitor = Schemas['LidarrArtistIn']['monitor']

/** Artists by this name as Lidarr's metadata knows them. */
export function useLidarrArtistLookup(name: string) {
  return useQuery({
    queryKey: ['lidarr', 'artists', name],
    staleTime: 5 * 60_000,
    retry: false,
    queryFn: () => unwrap(api.GET('/api/lidarr/artists/lookup', { params: { query: { name } } })),
  })
}

export function useAddLidarrArtist() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { mbid: string; monitor: ArtistMonitor; search: boolean }) => unwrap(api.POST('/api/lidarr/artists', { body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['lidarr', 'artists'] }),
  })
}
