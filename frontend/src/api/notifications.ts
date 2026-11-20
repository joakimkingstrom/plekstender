import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type AppNotification = Schemas['NotificationOut']
const KEY = ['notifications'] as const

export function useNotifications() {
  return useQuery({
    queryKey: KEY,
    queryFn: () => unwrap(api.GET('/api/notifications')),
    refetchInterval: 2 * 60_000,
  })
}

export function useNotificationActions() {
  const qc = useQueryClient()
  const done = () => qc.invalidateQueries({ queryKey: KEY })
  const read = useMutation({
    mutationFn: (ids: number[]) => unwrap(api.POST('/api/notifications/read', { body: { ids } })),
    onSuccess: done,
  })
  const dismiss = useMutation({
    mutationFn: (id: number) =>
      unwrap(api.POST('/api/notifications/{notification_id}/dismiss', { params: { path: { notification_id: id } } })),
    onSuccess: done,
  })
  const hide = useMutation({
    mutationFn: (id: number) =>
      unwrap(api.POST('/api/notifications/{notification_id}/hide', { params: { path: { notification_id: id } } })),
    onSuccess: done,
  })
  return { read, dismiss, hide }
}
