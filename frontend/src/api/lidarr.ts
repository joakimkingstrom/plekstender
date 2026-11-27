import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type LidarrAdmin = Schemas['LidarrAdminOut']
export type LidarrOptions = Schemas['LidarrOptionsOut']
export type LidarrSetup = Schemas['LidarrIn']

export const lidarrKeys = {
  admin: ['admin', 'lidarr'] as const,
  status: ['lidarr', 'status'] as const,
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
