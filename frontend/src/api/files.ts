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
        void qc.invalidateQueries({ queryKey: [...filesKeys.all(serverId), 'unknown'] })
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

// --- identifying files by sound ---------------------------------------------------------------

export type UnknownKind = Schemas['IdentifyIn']['kind'] & string
export type UnknownFile = Schemas['UnknownFileOut']
export type IdentityChoice = Schemas['IdentityChoice']
export type IdentifyStatus = Schemas['IdentifyStatusOut']

const identifyKeys = {
  status: (serverId: number) => [...filesKeys.all(serverId), 'identify'] as const,
  unknown: (serverId: number, kind: string) => [...filesKeys.all(serverId), 'unknown', kind] as const,
}

/** Whether an AcoustID key is set, and the latest identify job (polled while one runs). When
 * a job finishes, the lists of files and the album suggestions are fetched again. */
export function useIdentifyStatus(serverId: number) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: identifyKeys.status(serverId),
    queryFn: async () => {
      const before = qc.getQueryData<IdentifyStatus>(identifyKeys.status(serverId))
      const data = await unwrap(
        api.GET('/api/servers/{server_id}/files/identify', { params: { path: { server_id: serverId } } }),
      )
      if (before?.active && !data.active) {
        qc.invalidateQueries({ queryKey: [...filesKeys.all(serverId), 'unknown'] })
        qc.invalidateQueries({ queryKey: [...filesKeys.all(serverId), 'suggested'] })
      }
      return data
    },
    refetchInterval: (query) => (query.state.data?.active ? 1500 : false),
  })
}

export function useStartIdentify(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['IdentifyIn']) =>
      unwrap(api.POST('/api/servers/{server_id}/files/identify', { params: { path: { server_id: serverId } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: identifyKeys.status(serverId) }),
  })
}

export function useUnknownFiles(serverId: number, kind: UnknownKind) {
  return useQuery({
    queryKey: identifyKeys.unknown(serverId, kind),
    placeholderData: keepPreviousData,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/files/unknown', { params: { path: { server_id: serverId }, query: { kind } } }),
      ),
  })
}

/** The tags the chosen matches would change (nothing written). */
export function useIdentifyPreview(serverId: number, choices: IdentityChoice[]) {
  return useQuery({
    queryKey: [...filesKeys.all(serverId), 'identify-preview', choices],
    gcTime: 0,
    retry: false,
    queryFn: () =>
      unwrap(
        api.POST('/api/servers/{server_id}/files/identify/preview', {
          params: { path: { server_id: serverId } },
          body: { choices },
        }),
      ),
  })
}

export function useIdentifyApply(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (choices: IdentityChoice[]) =>
      unwrap(
        api.POST('/api/servers/{server_id}/files/identify/apply', {
          params: { path: { server_id: serverId } },
          body: { choices },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...filesKeys.all(serverId), 'history'] }),
  })
}

/** Releases an album's files sound like (once identified). */
export function useMbSuggested(serverId: number, albumKey: number) {
  return useQuery({
    queryKey: [...filesKeys.all(serverId), 'suggested', albumKey],
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/files/albums/{album_key}/musicbrainz/suggested', {
          params: { path: { server_id: serverId, album_key: albumKey } },
        }),
      ),
  })
}

export function useAcoustIdKey(enabled: boolean) {
  return useQuery({ queryKey: ['admin', 'acoustid'], enabled, queryFn: () => unwrap(api.GET('/api/admin/acoustid')) })
}

export function useSetAcoustIdKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (apiKey: string | null) => unwrap(api.PUT('/api/admin/acoustid', { body: { api_key: apiKey } })),
    onSuccess: (data) => {
      qc.setQueryData(['admin', 'acoustid'], data)
      qc.invalidateQueries({ queryKey: ['servers'] })
    },
  })
}

// --- renaming and moving files ----------------------------------------------------------------

export type RenamePlan = Schemas['RenamePlanOut']
export const DEFAULT_TEMPLATE = '{albumartist}/{album}/[{disc}-][{track:02} - ]{title}'

/** Start a dry run: where an album's or an artist's files would go (nothing moves). */
export function usePlanRenames(serverId: number) {
  return useMutation({
    mutationFn: (body: Schemas['RenameIn']) =>
      unwrap(api.POST('/api/servers/{server_id}/files/renames', { params: { path: { server_id: serverId } }, body })),
  })
}

/** A dry run's result (polled until the worker has worked it out). */
export function useRenamePlan(serverId: number, jobId: number | null) {
  return useQuery({
    queryKey: [...filesKeys.all(serverId), 'renames', jobId],
    enabled: jobId !== null,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/files/renames/{job_id}', {
          params: { path: { server_id: serverId, job_id: jobId! } },
        }),
      ),
    refetchInterval: (query) => {
      const status = query.state.data?.job.status
      return status === 'queued' || status === 'running' ? 1000 : false
    },
  })
}

export function useApplyRenames(serverId: number) {
  return useQueueTags(serverId, (jobId: number) =>
    unwrap(
      api.POST('/api/servers/{server_id}/files/renames/{job_id}/apply', {
        params: { path: { server_id: serverId, job_id: jobId } },
      }),
    ),
  )
}

// --- .nfo files ---------------------------------------------------------------------------------

export type Nfo = Schemas['NfoOut']

/** The album.nfo in an album's folder, if there is one. */
export function useAlbumNfo(serverId: number, albumKey: number) {
  return useQuery({
    queryKey: [...filesKeys.album(serverId, albumKey), 'nfo'],
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/files/albums/{album_key}/nfo', {
          params: { path: { server_id: serverId, album_key: albumKey } },
        }),
      ),
  })
}

/** The artist.nfo above an artist's albums, if there is one. */
export function useArtistNfo(serverId: number, artistKey: number) {
  return useQuery({
    queryKey: [...filesKeys.all(serverId), 'artist-nfo', artistKey],
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/files/artists/{artist_key}/nfo', {
          params: { path: { server_id: serverId, artist_key: artistKey } },
        }),
      ),
  })
}
