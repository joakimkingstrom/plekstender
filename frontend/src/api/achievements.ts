import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'
import { localTimeZone } from './stats'

export type Achievement = Schemas['AchievementOut']
const KEY = ['achievements'] as const

/** Every achievement with your progress (unlocks any just reached). */
export function useAchievements() {
  return useQuery({
    queryKey: [...KEY, 'all'],
    queryFn: () => unwrap(api.GET('/api/achievements', { params: { query: { tz: localTimeZone() } } })),
  })
}

/** Unlocked ones not celebrated yet (checked every few minutes while the app is open). */
export function useNewAchievements(enabled: boolean) {
  return useQuery({
    queryKey: [...KEY, 'new'],
    enabled,
    refetchInterval: 3 * 60_000,
    queryFn: () => unwrap(api.GET('/api/achievements', { params: { query: { tz: localTimeZone(), new: true } } })),
  })
}

export function useSeenAchievements() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids: string[]) => unwrap(api.POST('/api/achievements/seen', { body: { ids } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
}

export function useAchievementSettings(enabled: boolean) {
  return useQuery({ queryKey: [...KEY, 'admin'], enabled, queryFn: () => unwrap(api.GET('/api/admin/achievements')) })
}

export function useSetAchievementSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (on: boolean) => unwrap(api.PUT('/api/admin/achievements', { body: { enabled: on } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
}

/** A symbol per kind of achievement. */
export const GROUP_SYMBOL: Record<Achievement['group'], string> = {
  library: '♜',
  rating: '★',
  game: '♪',
  gigs: '✦',
  connections: '⌁',
  listening: '◉',
}
