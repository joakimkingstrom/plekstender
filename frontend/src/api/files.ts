import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type FilesSummary = Schemas['FilesSummaryOut']
export type TrackFile = Schemas['TrackFileOut']

const filesKeys = {
  all: (serverId: number) => ['servers', serverId, 'files'] as const,
  summary: (serverId: number) => ['servers', serverId, 'files', 'summary'] as const,
  album: (serverId: number, key: number) => ['servers', serverId, 'files', 'album', key] as const,
}

export function useFilesSummary(serverId: number) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: filesKeys.summary(serverId),
    queryFn: async () => {
      const data = await unwrap(
        api.GET('/api/servers/{server_id}/files/summary', { params: { path: { server_id: serverId } } }),
      )
      // A scan just finished: albums' file views may have changed.
      const previous = qc.getQueryData<FilesSummary>(filesKeys.summary(serverId))
      if (previous?.active && !data.active) void qc.invalidateQueries({ queryKey: filesKeys.all(serverId) })
      return data
    },
    refetchInterval: (query) => (query.state.data?.active ? 1500 : false),
  })
}

export function useStartScan(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/servers/{server_id}/files/scans', { params: { path: { server_id: serverId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: filesKeys.summary(serverId) }),
  })
}

export function useAlbumFiles(serverId: number, albumKey: number, enabled: boolean) {
  return useQuery({
    queryKey: filesKeys.album(serverId, albumKey),
    enabled,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/files/albums/{album_key}', {
          params: { path: { server_id: serverId, album_key: albumKey } },
        }),
      ),
  })
}

export type TagChangeSet = Schemas['TagChangeSetOut']
export type TagChange = Schemas['TagChangeOut']
export type TagEdit = Schemas['TagEditChange']

export function useSetTagEditing(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (on: boolean) =>
      unwrap(
        api.PUT('/api/servers/{server_id}/files/settings', {
          params: { path: { server_id: serverId } },
          body: { tag_editing: on },
        }),
      ),
    onSuccess: (data) => qc.setQueryData(filesKeys.summary(serverId), data),
  })
}

const busy = (sets: TagChangeSet[] | undefined) => sets?.some((s) => s.status === 'queued' || s.status === 'running')

/** Edits that touched an album's or an artist's files, newest first (polled while the
 * worker is writing; the files' tags are refreshed when it's done). */
export function useTagHistory(serverId: number, filter: { album?: number; artist?: number }) {
  const qc = useQueryClient()
  const key = [...filesKeys.all(serverId), 'history', filter] as const
  return useQuery({
    queryKey: key,
    queryFn: async () => {
      const data = await unwrap(
        api.GET('/api/servers/{server_id}/files/changes', {
          params: { path: { server_id: serverId }, query: filter },
        }),
      )
      if (busy(qc.getQueryData<TagChangeSet[]>(key)) && !busy(data)) {
        // The worker finished: the files' tags changed.
        void qc.invalidateQueries({ queryKey: [...filesKeys.all(serverId), 'album'] })
      }
      return data
    },
    refetchInterval: (query) => (busy(query.state.data) ? 1500 : false),
  })
}

function useQueueTags<T>(serverId: number, request: (body: T) => Promise<TagChangeSet>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: request,
    onSuccess: () => qc.invalidateQueries({ queryKey: [...filesKeys.all(serverId), 'history'] }),
  })
}

export function useEditTags(serverId: number) {
  return useQueueTags(serverId, (body: { description: string | null; changes: TagEdit[] }) =>
    unwrap(api.POST('/api/servers/{server_id}/files/edits', { params: { path: { server_id: serverId } }, body })),
  )
}

/** Undo picked changes, or a whole edit (`{ setId }`, any size). */
export function useUndoTags(serverId: number) {
  return useQueueTags(serverId, (what: number[] | { setId: number }) =>
    unwrap(
      api.POST('/api/servers/{server_id}/files/undo', {
        params: { path: { server_id: serverId } },
        body: Array.isArray(what) ? { change_ids: what } : { set_id: what.setId },
      }),
    ),
  )
}

export type PlexSyncRequest = Schemas['PlexSyncIn']
export type PlexSyncPreview = Schemas['PlexSyncPreviewOut']

/** What writing Plex's metadata to the files would change (nothing is written). */
export function usePlexSyncPreview(serverId: number) {
  return useMutation({
    mutationFn: (body: PlexSyncRequest) =>
      unwrap(api.POST('/api/servers/{server_id}/files/plex-sync/preview', { params: { path: { server_id: serverId } }, body })),
  })
}

export function usePlexSync(serverId: number) {
  return useQueueTags(serverId, (body: PlexSyncRequest) =>
    unwrap(api.POST('/api/servers/{server_id}/files/plex-sync', { params: { path: { server_id: serverId } }, body })),
  )
}

export type MbRelease = Schemas['MbReleaseSummary']
export type MbApply = Schemas['MbApplyIn']
export type MbPreview = Schemas['MbPreviewOut']

/** Releases on MusicBrainz (by album and artist, or a pasted release link). */
export function useMbSearch(serverId: number, search: { album: string; artist: string } | null) {
  return useQuery({
    queryKey: [...filesKeys.all(serverId), 'musicbrainz', 'search', search],
    enabled: search !== null && search.album.trim() !== '',
    staleTime: Infinity,
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/files/musicbrainz/search', {
          params: {
            path: { server_id: serverId },
            query: { album: search!.album.trim(), artist: search!.artist.trim() || undefined },
          },
        }),
      ),
  })
}

/** Which file is which track of a release, and the tags that would change (nothing written). */
export function useMbPreview(serverId: number, albumKey: number, body: MbApply | null) {
  return useQuery({
    queryKey: [...filesKeys.album(serverId, albumKey), 'musicbrainz', body],
    enabled: body !== null,
    staleTime: Infinity,
    retry: false,
    placeholderData: keepPreviousData,
    queryFn: () =>
      unwrap(
        api.POST('/api/servers/{server_id}/files/albums/{album_key}/musicbrainz/preview', {
          params: { path: { server_id: serverId, album_key: albumKey } },
          body: body!,
        }),
      ),
  })
}

export function useMbApply(serverId: number, albumKey: number) {
  return useQueueTags(serverId, (body: MbApply) =>
    unwrap(
      api.POST('/api/servers/{server_id}/files/albums/{album_key}/musicbrainz', {
        params: { path: { server_id: serverId, album_key: albumKey } },
        body,
      }),
    ),
  )
}

export function mbCoverUrl(serverId: number, releaseId: string) {
  return `/api/servers/${serverId}/files/musicbrainz/cover/${releaseId}`
}
