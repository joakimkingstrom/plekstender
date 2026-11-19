import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
