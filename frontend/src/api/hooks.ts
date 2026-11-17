import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api, unwrap, type Schemas } from './client'

export const keys = {
  me: ['me'] as const,
  health: ['health'] as const,
  servers: ['servers'] as const,
  available: ['servers', 'available'] as const,
  sections: (id: number) => ['servers', id, 'sections'] as const,
  adminAccess: ['admin', 'access'] as const,
  adminServers: ['admin', 'servers'] as const,
  adminUsers: ['admin', 'users'] as const,
}

/** The signed-in user, or null when signed out. */
export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: async () => {
      try {
        return await unwrap(api.GET('/api/auth/me'))
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    staleTime: 5 * 60_000,
  })
}

export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/auth/logout')),
    onSuccess: () => {
      qc.clear()
      qc.setQueryData(keys.me, null)
    },
  })
}

export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => unwrap(api.GET('/api/health')),
    refetchInterval: 60_000,
  })
}

export function useServers() {
  return useQuery({ queryKey: keys.servers, queryFn: () => unwrap(api.GET('/api/servers')) })
}

export function useAvailableServers() {
  return useQuery({
    queryKey: keys.available,
    queryFn: () => unwrap(api.GET('/api/servers/available')),
  })
}

export function useConnectServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (machineIdentifier: string) =>
      unwrap(api.POST('/api/servers', { body: { machine_identifier: machineIdentifier } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.servers }),
  })
}

export function useDisconnectServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE('/api/servers/{server_id}', { params: { path: { server_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.servers }),
  })
}

export function useSections(serverId: number) {
  return useQuery({
    queryKey: keys.sections(serverId),
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/sections', {
          params: { path: { server_id: serverId } },
        }),
      ),
  })
}

export function useSelectSections(serverId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (sectionKeys: string[]) =>
      unwrap(
        api.PUT('/api/servers/{server_id}/sections', {
          params: { path: { server_id: serverId } },
          body: { keys: sectionKeys },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.servers }),
  })
}

// --- admin -------------------------------------------------------------------------

export function useAdminAccess() {
  return useQuery({
    queryKey: keys.adminAccess,
    queryFn: () => unwrap(api.GET('/api/admin/access')),
  })
}

export function useUpdateAdminAccess() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['AccessSettingsIn']) =>
      unwrap(api.PUT('/api/admin/access', { body })),
    onSuccess: (data) => qc.setQueryData(keys.adminAccess, data),
  })
}

export function useAdminServers() {
  return useQuery({
    queryKey: keys.adminServers,
    queryFn: () => unwrap(api.GET('/api/admin/servers')),
  })
}

export function useApproveServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, approved }: { id: number; approved: boolean }) =>
      unwrap(
        api.PUT('/api/admin/servers/{server_id}', {
          params: { path: { server_id: id } },
          body: { approved },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.adminServers }),
  })
}

export function useAdminUsers() {
  return useQuery({ queryKey: keys.adminUsers, queryFn: () => unwrap(api.GET('/api/admin/users')) })
}

export function useUpdateUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, isAdmin }: { id: number; isAdmin: boolean }) =>
      unwrap(
        api.PUT('/api/admin/users/{user_id}', {
          params: { path: { user_id: id } },
          body: { is_admin: isAdmin },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.adminUsers }),
  })
}

export function useRemoveUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE('/api/admin/users/{user_id}', { params: { path: { user_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.adminUsers }),
  })
}
