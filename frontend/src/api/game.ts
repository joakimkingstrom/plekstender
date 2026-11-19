import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap, type Schemas } from './client'

export type GameMode = Schemas['GameCheckIn']['mode']
export type GameRound = Schemas['GameRoundOut']
export type GameSearchItem = Schemas['GameSearchItem']
export type GameStats = Schemas['GameStatsOut']

const gameKeys = {
  stats: ['game', 'stats'] as const,
  search: (serverId: number, kind: GameMode, q: string, artist: number | null) =>
    ['game', serverId, 'search', kind, q, artist] as const,
}

// Rounds are prepared ahead (the next card's clip is made while this one plays): the same
// song is used when the card comes up.
const rounds = new Map<string, Promise<GameRound>>()

export function fetchRound(serverId: number, mode: GameMode, ratingKey: number): Promise<GameRound> {
  const key = `${serverId}:${mode}:${ratingKey}`
  let round = rounds.get(key)
  if (!round) {
    round = unwrap(
      api.GET('/api/servers/{server_id}/game/round', {
        params: { path: { server_id: serverId }, query: { mode, rating_key: ratingKey } },
      }),
    )
    round.catch(() => rounds.delete(key))
    rounds.set(key, round)
    if (rounds.size > 50) rounds.delete(rounds.keys().next().value!)
  }
  return round
}

export function useGameSearch(serverId: number, kind: GameMode, q: string, artist: number | null, enabled: boolean) {
  return useQuery({
    queryKey: gameKeys.search(serverId, kind, q, artist),
    enabled,
    queryFn: () =>
      unwrap(
        api.GET('/api/servers/{server_id}/game/search', {
          params: { path: { server_id: serverId }, query: { kind, q, artist: artist ?? undefined } },
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  })
}

export function checkGuess(serverId: number, mode: GameMode, target: number, guess: number) {
  return unwrap(
    api.POST('/api/servers/{server_id}/game/check', {
      params: { path: { server_id: serverId } },
      body: { mode, target, guess },
    }),
  )
}

export function useGameStats() {
  return useQuery({ queryKey: gameKeys.stats, queryFn: () => unwrap(api.GET('/api/game/stats')) })
}

export function useRecordResult() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Schemas['GameResultIn']) => unwrap(api.POST('/api/game/results', { body })),
    onSuccess: (row) =>
      qc.setQueryData<GameStats[]>(gameKeys.stats, (old) => [...(old ?? []).filter((s) => s.mode !== row.mode), row]),
  })
}

// A song to listen to while rating a card (prepared ahead, like game rounds).
const samples = new Map<string, Promise<GameRound>>()

export function fetchSample(serverId: number, mode: GameMode, ratingKey: number): Promise<GameRound> {
  const key = `${serverId}:${mode}:${ratingKey}`
  let sample = samples.get(key)
  if (!sample) {
    sample = unwrap(
      api.GET('/api/servers/{server_id}/rate/sample', {
        params: { path: { server_id: serverId }, query: { mode, rating_key: ratingKey } },
      }),
    )
    sample.catch(() => samples.delete(key))
    samples.set(key, sample)
    if (samples.size > 50) samples.delete(samples.keys().next().value!)
  }
  return sample
}
